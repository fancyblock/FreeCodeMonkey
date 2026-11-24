"""主窗口：装配工作区、Provider 与 agent 循环。

窗口本身不含任何 agent 逻辑：它只负责把界面操作翻译成 runtime 调用，
再把 runtime 事件渲染成界面元素。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.agent import AgentEvent, AgentRuntime, Workspace
from app.agent import events as E
from app.agent.runtime import summarize_call, trim_history
from app.config import Settings
from app.constants import APP_NAME, APP_VERSION, DEFAULT_CONTEXT_WINDOW
from app.core import ProviderManager, get_spec
from app.ui.agent_bridge import AgentBridge
from app.ui.dialogs import FilePreviewDialog, SettingsDialog
from app.ui.widgets import ChatView, FileTree, PromptInput, PromptList
from app.workers import release_handle, run_in_thread

# 会改动工作区文件系统的工具；新增写类工具时要在这里登记
_MUTATING_TOOLS = frozenset({"edit_file", "run_command"})


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, settings: Settings, manager: ProviderManager) -> None:
        super().__init__()
        self._settings = settings
        self._manager = manager
        # 保持任务句柄的引用，防止被垃圾回收导致线程提前销毁
        self._active_handles: list = []
        # 会话内的多轮消息（不持久化）
        self._history: list[BaseMessage] = []
        self._runtime: AgentRuntime | None = None
        self._bridge: AgentBridge | None = None
        self._workspace_root = self._initial_workspace()
        # 本次任务是否改过文件；任务结束时决定要不要刷新文件树
        self._files_changed = False

        self._chat_view = ChatView()
        self._prompt_input = PromptInput()
        self._prompt_list = PromptList()
        self._file_tree = FileTree()
        self._provider_combo = QComboBox()
        self._workspace_label = QLabel()

        self._build_ui()
        self._create_actions()
        self._create_menus()
        self._connect_signals()
        self._refresh_providers()
        self._refresh_workspace_label()
        self._file_tree.set_root(self._workspace_root)
        self._restore_state()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(1020, 720)

        provider_bar = QHBoxLayout()
        provider_bar.addWidget(QLabel("使用模型："))
        provider_bar.addWidget(self._provider_combo, 1)

        choose_button = QPushButton("选择目录…")
        choose_button.clicked.connect(self._choose_workspace)
        self._workspace_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        workspace_bar = QHBoxLayout()
        workspace_bar.addWidget(QLabel("工作区："))
        workspace_bar.addWidget(self._workspace_label, 1)
        workspace_bar.addWidget(choose_button)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._chat_view)
        splitter.addWidget(self._prompt_input)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        # 左栏上下分：文件树在上、提示词列表在下，中间可拖动
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self._file_tree)
        left.addWidget(self._prompt_list)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)
        left.setCollapsible(0, False)
        left.setCollapsible(1, False)
        left.setMinimumWidth(180)
        left.setSizes([320, 200])

        # 左侧整列横跨对话区与输入框；窗口变窄时优先压缩对话区
        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(left)
        body.addWidget(splitter)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setCollapsible(0, False)
        body.setSizes([260, 760])
        self._body_splitter = body
        self._left_splitter = left

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(provider_bar)
        layout.addLayout(workspace_bar)
        layout.addWidget(body)
        self.setCentralWidget(container)

        self.statusBar().showMessage("就绪")

    def _create_actions(self) -> None:
        self._workspace_action = QAction("选择工作目录…", self)
        self._workspace_action.triggered.connect(self._choose_workspace)

        self._settings_action = QAction("设置…", self)
        self._settings_action.triggered.connect(self._open_settings)

        self._clear_action = QAction("清空对话", self)
        self._clear_action.triggered.connect(self._clear_history)

        self._about_action = QAction("关于", self)
        self._about_action.triggered.connect(self._show_about)

        self._quit_action = QAction("退出", self)
        self._quit_action.triggered.connect(self.close)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._workspace_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self._clear_action)
        edit_menu.addAction(self._settings_action)

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self._about_action)

    def _connect_signals(self) -> None:
        self._prompt_input.submitted.connect(self._on_submit)
        self._prompt_input.stop_requested.connect(self._on_stop)
        self._prompt_list.prompt_activated.connect(self._jump_to_prompt)
        self._file_tree.file_activated.connect(self._open_preview)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)

    # ---- Provider ----
    def _refresh_providers(self) -> None:
        """重新填充下拉框并恢复选中项。"""
        self._provider_combo.blockSignals(True)
        self._provider_combo.clear()
        configs = self._manager.providers
        for config in configs:
            label = f"{config.name} · {get_spec(config.type).label}"
            self._provider_combo.addItem(label, config.id)

        active_id = self._manager.active_id
        if active_id:
            index = self._provider_combo.findData(active_id)
            if index >= 0:
                self._provider_combo.setCurrentIndex(index)
        elif configs:
            self._provider_combo.setCurrentIndex(0)
            first_id = self._provider_combo.itemData(0)
            if first_id is not None:
                self._manager.active_id = str(first_id)
        self._provider_combo.blockSignals(False)

        has_provider = len(configs) > 0
        self._prompt_input.setDisabled(not has_provider)
        self._prompt_input.set_context_window(self._active_context_window())
        if has_provider:
            self.statusBar().showMessage("就绪")
        else:
            self.statusBar().showMessage("请先在「编辑 → 设置」中添加 LLM Provider")

    @pyqtSlot(int)
    def _on_provider_changed(self, index: int) -> None:
        provider_id = self._provider_combo.itemData(index)
        if provider_id:
            self._manager.active_id = str(provider_id)
            self._prompt_input.set_context_window(self._active_context_window())

    def _active_context_window(self) -> int:
        config = self._manager.active_config
        return config.context_window if config is not None else DEFAULT_CONTEXT_WINDOW

    # ---- 工作区 ----
    def _initial_workspace(self) -> Path:
        configured = self._settings.workspace_root.strip()
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
        return Path.cwd().resolve()

    def _refresh_workspace_label(self) -> None:
        path = str(self._workspace_root)
        self._workspace_label.setText(path)
        self._workspace_label.setToolTip(path)

    def _choose_workspace(self) -> None:
        if self._active_handles:
            self._chat_view.append_system("任务执行中，等它结束再切换工作区。")
            return
        directory = QFileDialog.getExistingDirectory(
            self, "选择 agent 的工作目录", str(self._workspace_root)
        )
        if not directory:
            return
        self._workspace_root = Path(directory).resolve()
        self._settings.workspace_root = str(self._workspace_root)
        self._settings.sync()
        self._refresh_workspace_label()
        self._file_tree.set_root(self._workspace_root)
        self._files_changed = False
        self._chat_view.append_system(f"工作区已切换到 {self._workspace_root}")

    @pyqtSlot(str)
    def _open_preview(self, rel_path: str) -> None:
        """双击文件树里的文件：弹只读预览。"""
        FilePreviewDialog(self._workspace_root, rel_path, self).exec()

    # ---- 状态持久化 ----
    def _restore_state(self) -> None:
        geometry = self._settings.geometry
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._active_handles:
            # 先让工作线程有路可走：撤掉挂起的审批、请求停止
            if self._bridge is not None:
                self._bridge.abort_pending()
            if self._runtime is not None:
                self._runtime.cancel()

            stuck = []
            for handle in list(self._active_handles):
                handle.thread.quit()
                if not handle.thread.wait(3000):
                    stuck.append(handle)
            if stuck:
                # 线程还活着就销毁窗口，等于让进程直接 abort
                self._chat_view.append_system("任务仍在执行，请点「停止」后再关闭窗口。")
                event.ignore()
                return
            for handle in list(self._active_handles):
                self._release_handle(handle)

        self._settings.geometry = self.saveGeometry()
        self._settings.sync()
        super().closeEvent(event)

    # ---- 提交任务 ----
    @pyqtSlot(str)
    def _on_submit(self, text: str) -> None:
        task = text.strip()
        if not task:
            return

        config = self._manager.active_config
        if config is None:
            self._chat_view.append_system("请先添加并选择一个 LLM Provider。")
            return

        mode = self._prompt_input.mode()
        try:
            workspace = Workspace(self._workspace_root, read_only=(mode == "plan"))
        except ValueError as exc:
            self._chat_view.append_system(f"工作区不可用：{exc}")
            return

        try:
            model = self._manager.build_model(config)
        except ValueError as exc:
            self._chat_view.append_system(f"配置错误：{exc}")
            return

        if self._active_handles:
            self._chat_view.append_system("上一个任务还在执行，请先停止或等待完成。")
            return

        mark = self._chat_view.append_message("user", task)
        self._prompt_list.add_prompt(task, mark)
        self._files_changed = False
        self._prompt_input.clear()
        self._prompt_input.set_running(True)
        self.statusBar().showMessage(f"正在调用 {config.name}（{mode}）…")

        bridge = AgentBridge(self)
        bridge.agent_event.connect(self._on_agent_event)
        runtime = AgentRuntime(
            model,
            workspace,
            bridge,
            mode=mode,
            context_window=config.context_window,
        )
        self._bridge = bridge
        self._runtime = runtime

        handle = run_in_thread(runtime.run, task, list(self._history), bridge.agent_event.emit)
        self._active_handles.append(handle)
        handle.worker.result.connect(lambda messages: self._on_run_done(messages, handle))
        handle.worker.failed.connect(lambda msg: self._on_run_failed(msg, handle))
        # 线程生命周期归界面管：只有 finished 之后才允许释放句柄，
        # 提前释放会让 PyQt 销毁运行中的 QThread，进程直接崩掉。
        handle.thread.finished.connect(self._on_thread_finished)

    @pyqtSlot(object)
    def _on_agent_event(self, event: AgentEvent) -> None:
        """把 runtime 事件渲染成步骤流。"""
        kind = event.kind
        if kind == E.STEP:
            self._chat_view.append_step(f"— 第 {event.step} 步 —")
            self.statusBar().showMessage(f"第 {event.step} 步：推理中")
        elif kind == E.THOUGHT:
            self._chat_view.append_step("", event.text, "muted")
        elif kind == E.TOOL_CALL:
            summary = summarize_call(event.tool, event.args)
            self._chat_view.append_step(f"▸ {event.tool}  {summary}", tone="info")
            self.statusBar().showMessage(f"执行 {event.tool} …")
        elif kind == E.TOOL_RESULT:
            mark = "→" if event.ok else "✗"
            tone = "ok" if event.ok else "bad"
            self._chat_view.append_step(f"{mark} {event.tool}", event.text, tone)
            self.statusBar().showMessage(f"{event.tool} 完成")
            # 一个任务里可能改很多次；只记标记，任务结束再统一刷新一次
            if event.ok and event.tool in _MUTATING_TOOLS:
                self._files_changed = True
        elif kind == E.DENIED:
            self._chat_view.append_step(f"✗ 已拒绝 {event.tool}", event.text, "bad")
            self.statusBar().showMessage("你拒绝了一个操作")
        elif kind == E.CONTEXT:
            self._prompt_input.set_context_usage(event.used_tokens, event.window_tokens)
        elif kind == E.DONE:
            self._chat_view.append_message("assistant", event.text)
            self.statusBar().showMessage("就绪")
        elif kind == E.ERROR:
            self._chat_view.append_system(event.text)
            self.statusBar().showMessage("出错")

    def _on_run_done(self, messages: list[BaseMessage], handle) -> None:
        self._history = trim_history(list(messages))
        self._refresh_tree_if_changed()
        self._finish_task("就绪")

    def _on_run_failed(self, message: str, handle) -> None:
        self._chat_view.append_system(f"错误：{message}")
        self._refresh_tree_if_changed()
        self._finish_task("出错")

    def _refresh_tree_if_changed(self) -> None:
        """只有 agent 真的改过文件才重建文件树，纯读任务不动它。"""
        if not self._files_changed:
            return
        self._files_changed = False
        self._file_tree.refresh()

    def _on_stop(self) -> None:
        if self._runtime is None:
            return
        self._runtime.cancel()
        self.statusBar().showMessage("已请求停止，当前步骤结束后生效…")

    def _finish_task(self, status: str) -> None:
        """只复位界面状态；句柄要等线程真正结束再由 _on_thread_finished 释放。"""
        self._prompt_input.set_running(False)
        self.statusBar().showMessage(status)

    @pyqtSlot(str)
    def _jump_to_prompt(self, mark: str) -> None:
        """把对话区滚动到该提示词的消息处并高亮。"""
        self._chat_view.scroll_to_mark(mark)

    @pyqtSlot()
    def _on_thread_finished(self) -> None:
        thread = self.sender()
        for handle in list(self._active_handles):
            try:
                matched = handle.thread is thread
            except RuntimeError:
                matched = False
            if matched:
                self._release_handle(handle)
                break

    def _release_handle(self, handle) -> None:
        if handle in self._active_handles:
            self._active_handles.remove(handle)
        release_handle(handle)
        if not self._active_handles:
            self._runtime = None
            self._bridge = None

    # ---- 菜单动作 ----
    def _clear_history(self) -> None:
        if self._active_handles:
            self._chat_view.append_system("任务执行中，无法清空对话。")
            return
        self._history.clear()
        self._prompt_list.clear_prompts()
        self._prompt_input.reset_context()
        self._chat_view.clear_history()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._manager, self)
        dialog.exec()
        self._refresh_providers()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "一个在本机工作的编程智能体：能读文件、搜代码、改代码、跑命令。<br>"
            "写文件与执行命令需要你逐次确认。<br><br>"
            "在「编辑 → 设置」中配置模型，在顶部选择工作区目录。",
        )
