"""主窗口：装配工作区、Provider 与 agent 循环。

窗口本身不含任何 agent 逻辑：它只负责把界面操作翻译成 runtime 调用，
再把 runtime 事件渲染成界面元素。
"""

from __future__ import annotations

import time
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
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.agent import (
    AgentEvent,
    AgentRuntime,
    ApprovalDecision,
    ApprovalRequest,
    Workspace,
)
from app.agent import events as E
from app.agent.runtime import summarize_call, trim_history
from app.config import Settings
from app.constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_CONTEXT_WINDOW,
    MAX_SESSIONS,
    MAX_WORKSPACE_HISTORY,
)
from app.core import ProviderManager, get_spec
from app.ui import sound
from app.ui.agent_bridge import AgentBridge
from app.ui.dialogs import FilePreviewDialog, SettingsDialog
from app.ui.export import (
    default_bulk_export_name,
    default_export_dir,
    default_export_name,
    session_to_markdown,
    sessions_to_markdown,
    write_markdown,
)
from app.ui.icons import icon
from app.ui.sessions import DEFAULT_TITLE, Session, SessionStore, make_title
from app.ui.widgets import (
    ChatView,
    FileTree,
    PromptInput,
    PromptList,
    SessionList,
)
from app.workers import release_handle, run_in_thread

# 会改动工作区文件系统的工具；新增写类工具时要在这里登记
_MUTATING_TOOLS = frozenset({"edit_file", "run_command"})

# 下拉框里固定那一项「选择目录…」的标识
_WORKSPACE_PICK = "__pick_directory__"


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        settings: Settings,
        manager: ProviderManager,
        session_store: SessionStore | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._manager = manager
        # 测试与多实例场景可以注入自己的存储位置
        self._session_store = session_store or SessionStore()
        self._sessions: list[Session] = []
        self._active_session_id = ""
        # 保持任务句柄的引用，防止被垃圾回收导致线程提前销毁
        self._active_handles: list = []
        # 会话内的多轮消息（不持久化）
        self._history: list[BaseMessage] = []
        self._runtime: AgentRuntime | None = None
        self._bridge: AgentBridge | None = None
        self._workspace_root = self._initial_workspace()
        self._workspace_history = self._load_workspace_history()
        # 本次任务是否改过文件；任务结束时决定要不要刷新文件树
        self._files_changed = False
        # 确认卡出现前的上下分栏比例，确认完按原样还回去
        self._input_sizes: list[int] | None = None

        self._chat_view = ChatView()
        self._prompt_input = PromptInput()
        self._prompt_list = PromptList()
        self._session_list = SessionList()
        self._file_tree = FileTree()
        self._provider_combo = QComboBox()
        self._workspace_combo = QComboBox()

        self._build_ui()
        self._create_actions()
        self._create_menus()
        self._connect_signals()
        self._refresh_providers()
        self._sync_workspace_combo()
        self._file_tree.set_root(self._workspace_root)
        self._restore_state()
        self._restore_sessions()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(1120, 760)

        # 下拉框列出用过的目录和固定的「选择目录…」；长路径靠悬停看全
        self._workspace_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._workspace_combo.setMinimumContentsLength(24)
        self._workspace_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._workspace_combo.setToolTip(str(self._workspace_root))
        self._workspace_combo.activated.connect(self._on_workspace_activated)

        # 模型与工作区合成一行，省下一条栏的垂直空间
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(QLabel("使用模型："))
        toolbar.addWidget(self._provider_combo, 3)
        toolbar.addSpacing(10)
        toolbar.addWidget(QLabel("工作区："))
        toolbar.addWidget(self._workspace_combo, 2)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._chat_view)
        splitter.addWidget(self._prompt_input)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(6)
        self._main_splitter = splitter

        # 左栏上中下三段：文件树 / 会话 / 提示词，中间都可拖动
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self._file_tree)
        left.addWidget(self._session_list)
        left.addWidget(self._prompt_list)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 3)
        left.setStretchFactor(2, 2)
        left.setCollapsible(0, False)
        left.setCollapsible(1, False)
        left.setCollapsible(2, False)
        left.setMinimumWidth(180)
        # 会话面板底部多了两个导出按钮，留够高度让列表至少能显示几行
        left.setSizes([250, 220, 160])
        left.setHandleWidth(6)

        # 左侧整列横跨对话区与输入框；窗口变窄时优先压缩对话区
        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(left)
        body.addWidget(splitter)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setCollapsible(0, False)
        body.setSizes([270, 800])
        body.setHandleWidth(6)
        self._body_splitter = body
        self._left_splitter = left

        container = QWidget()
        # 全局 QSS 靠这个 objectName 给窗口底色
        container.setObjectName("root")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(body)
        self.setCentralWidget(container)

        self.statusBar().showMessage("就绪")

    def _create_actions(self) -> None:
        self._workspace_action = QAction("选择工作目录…", self)
        self._workspace_action.setIcon(icon("folder", size=14))
        self._workspace_action.triggered.connect(self._choose_workspace)

        self._settings_action = QAction("设置…", self)
        self._settings_action.setIcon(icon("settings", size=14))
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
        self._prompt_input.new_session_requested.connect(self._on_new_session)
        self._prompt_input.approval_resolved.connect(self._on_approval_resolved)
        self._prompt_input.approval_size_changed.connect(self._fit_input_pane)
        self._prompt_list.prompt_activated.connect(self._jump_to_prompt)
        self._session_list.session_activated.connect(self._on_session_activated)
        self._session_list.session_delete_requested.connect(self._on_delete_session)
        self._session_list.export_current_requested.connect(self._on_export_current)
        self._session_list.export_all_requested.connect(self._on_export_all)
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

    def _load_workspace_history(self) -> list[str]:
        """读历史目录：去重、保持原有顺序（最近的在前）。"""
        seen: list[str] = []
        for path in self._settings.workspace_history:
            if path not in seen:
                seen.append(path)
        return seen[:MAX_WORKSPACE_HISTORY]

    def _remember_workspace(self, root: Path) -> None:
        """把目录挪到历史最前面，并落盘。"""
        path = str(root)
        self._workspace_history = [
            path,
            *(item for item in self._workspace_history if item != path),
        ][:MAX_WORKSPACE_HISTORY]
        self._settings.workspace_history = self._workspace_history
        self._settings.sync()

    def _forget_workspace(self, path: str) -> None:
        """目录已经不存在：从历史和下拉框里移除。"""
        if path not in self._workspace_history:
            return
        self._workspace_history = [
            item for item in self._workspace_history if item != path
        ]
        self._settings.workspace_history = self._workspace_history
        self._settings.sync()

    def _sync_workspace_combo(self) -> None:
        """按「当前目录 → 其他历史目录 → 分隔线 → 选择目录…」重建下拉项。"""
        combo = self._workspace_combo
        current = str(self._workspace_root)
        entries = [current]
        for path in self._workspace_history:
            if path not in entries:
                entries.append(path)

        combo.blockSignals(True)
        try:
            combo.clear()
            for path in entries:
                combo.addItem(path, path)
            combo.insertSeparator(combo.count())
            combo.addItem("选择目录…", _WORKSPACE_PICK)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else combo.count() - 1)
            combo.setToolTip(current)
        finally:
            combo.blockSignals(False)

    @pyqtSlot(int)
    def _on_workspace_activated(self, index: int) -> None:
        """用户从下拉框里选了某一项。"""
        data = self._workspace_combo.itemData(index)
        if data == _WORKSPACE_PICK:
            self._choose_workspace()
            return
        if not isinstance(data, str) or not data:
            # 分隔线之类没有实际目录的项，把显示恢复到当前工作区
            self._sync_workspace_combo()
            return

        target = Path(data)
        if not target.is_dir():
            self._chat_view.append_system(f"目录已不存在，已从列表移除：{data}")
            self._forget_workspace(data)
            self._sync_workspace_combo()
            return

        target = target.resolve()
        if target == self._workspace_root:
            self._sync_workspace_combo()
            return
        if self._active_handles:
            self._chat_view.append_system("任务执行中，等它结束再切换工作区。")
            self._sync_workspace_combo()
            return
        self._apply_workspace(target)

    def _choose_workspace(self) -> None:
        if self._active_handles:
            self._chat_view.append_system("任务执行中，等它结束再切换工作区。")
            self._sync_workspace_combo()
            return
        directory = QFileDialog.getExistingDirectory(
            self, "选择 agent 的工作目录", str(self._workspace_root)
        )
        if not directory:
            self._sync_workspace_combo()
            return
        path = Path(directory)
        if not path.is_dir():
            self._chat_view.append_system(f"目录不可用：{directory}")
            self._sync_workspace_combo()
            return
        self._apply_workspace(path.resolve())

    def _apply_workspace(self, root: Path) -> None:
        """切换工作区：更新配置、文件树与下拉列表。"""
        previous = self._workspace_root
        self._workspace_root = root
        self._settings.workspace_root = str(root)
        # 先把上一个目录留在历史里，再把新目录顶到最前
        self._remember_workspace(previous)
        self._remember_workspace(root)
        self._sync_workspace_combo()
        self._file_tree.set_root(root)
        self._files_changed = False
        self._chat_view.append_system(f"工作区已切换到 {root}")

    @pyqtSlot(str)
    def _open_preview(self, rel_path: str) -> None:
        """双击文件树里的文件：弹只读预览。"""
        FilePreviewDialog(self._workspace_root, rel_path, self).exec()

    # ---- 会话 ----
    def _restore_sessions(self) -> None:
        """启动时读出会话列表，并把上次激活的那个还原到界面。"""
        sessions, active_id = self._session_store.load()
        self._sessions = sessions[:MAX_SESSIONS]
        if not self._sessions:
            self._sessions = [Session.create()]
        known = {session.id for session in self._sessions}
        self._active_session_id = active_id if active_id in known else self._sessions[0].id
        self._sync_session_list()
        self._load_session_content(self._active_session())

    def _active_session(self) -> Session:
        for session in self._sessions:
            if session.id == self._active_session_id:
                return session
        # 兜底：数据被外部改坏时补一个，避免后续到处判空
        session = Session.create()
        self._sessions.insert(0, session)
        self._active_session_id = session.id
        return session

    def _sync_session_list(self) -> None:
        rows = [
            (
                session.id,
                session.title,
                time.strftime("%Y-%m-%d %H:%M", time.localtime(session.created_at)),
            )
            for session in self._sessions
        ]
        self._session_list.set_sessions(rows)
        self._session_list.select(self._active_session_id)

    def _load_session_content(self, session: Session) -> None:
        """把某个会话的记录铺回对话区与提示词列表，并恢复 agent 记忆。"""
        user_prompts = self._chat_view.replay(session.entries)
        self._prompt_list.clear_prompts()
        for text, mark in user_prompts:
            self._prompt_list.add_prompt(text, mark)
        self._history = session.history()
        self._files_changed = False
        self._prompt_input.reset_context()
        self._session_list.select(session.id)

    def _save_active_session(self, touch: bool = True) -> None:
        """把界面上的现状写回当前会话并落盘。

        ``touch=False`` 只同步内容、不更新修改时间——导出这类只读操作不该
        让会话看起来「刚被编辑过」。
        """
        session = self._active_session()
        session.entries = self._chat_view.entries()
        session.set_history(self._history)
        if touch:
            session.updated_at = time.time()
        titled = self._refresh_title(session)
        self._session_store.save(self._sessions, self._active_session_id)
        if titled:
            self._sync_session_list()

    @staticmethod
    def _refresh_title(session: Session) -> bool:
        """还没有标题时，用第一条用户消息补上；返回标题是否变了。

        调用前 session.entries 必须已经是最新的（_save_active_session 会先同步）。
        """
        if session.title != DEFAULT_TITLE:
            return False
        for entry in session.entries:
            if (
                isinstance(entry, (list, tuple))
                and len(entry) >= 3
                and entry[0] == "msg"
                and entry[1] == "user"
            ):
                session.title = make_title(str(entry[2]))
                return True
        return False

    def _switch_session(self, session: Session) -> None:
        """切到另一个会话：先存档当前，再铺开目标会话。"""
        self._save_active_session()
        self._active_session_id = session.id
        self._load_session_content(session)
        self._session_store.save(self._sessions, self._active_session_id)

    @pyqtSlot(str)
    def _on_session_activated(self, session_id: str) -> None:
        """双击会话列表里的一项。"""
        if self._active_handles:
            self.statusBar().showMessage("任务执行中，等它结束再切换会话。")
            self._session_list.select(self._active_session_id)
            return
        if not session_id or session_id == self._active_session_id:
            return
        target = next(
            (item for item in self._sessions if item.id == session_id), None
        )
        if target is None:
            self._restore_sessions()
            return
        self._switch_session(target)
        self.statusBar().showMessage(f"已切换到会话「{target.title}」")

    @pyqtSlot()
    def _on_new_session(self) -> None:
        """新建一轮会话：当前会话留在列表里，界面换成空白的一份。"""
        if self._active_handles:
            self.statusBar().showMessage("任务执行中，等它结束再新建会话。")
            return
        self._save_active_session()
        session = Session.create()
        self._sessions.insert(0, session)
        # 超出上限只丢最旧的；刚建的这个在最前面，不会被丢
        del self._sessions[MAX_SESSIONS:]
        self._active_session_id = session.id
        self._sync_session_list()
        self._load_session_content(session)
        self._session_store.save(self._sessions, self._active_session_id)
        self.statusBar().showMessage("已新建会话")

    @pyqtSlot(str)
    def _on_delete_session(self, session_id: str) -> None:
        """右键删除一个会话；删的是当前会话时自动切到相邻的一个。"""
        if self._active_handles:
            self.statusBar().showMessage("任务执行中，等它结束再删除会话。")
            return
        target = next((item for item in self._sessions if item.id == session_id), None)
        if target is None:
            return
        if not self._confirm_delete(target):
            return

        was_active = target.id == self._active_session_id
        index = self._sessions.index(target)
        self._sessions.remove(target)

        if not self._sessions:
            # 至少留一个空白会话，否则界面没有可编辑的目标
            replacement = Session.create()
            self._sessions.append(replacement)
            self._active_session_id = replacement.id
            self._sync_session_list()
            self._load_session_content(replacement)
        elif was_active:
            # 用原来的位置接着往后挑一个，没有就取前一个
            neighbour = self._sessions[min(index, len(self._sessions) - 1)]
            self._active_session_id = neighbour.id
            self._sync_session_list()
            self._load_session_content(neighbour)
        else:
            self._sync_session_list()

        self._session_store.save(self._sessions, self._active_session_id)
        self.statusBar().showMessage(f"已删除会话「{target.title}」")

    # ---- 导出 ----
    @pyqtSlot()
    def _on_export_current(self) -> None:
        """导出当前会话；先存档，保证导出的是屏幕上的最新内容。"""
        self._save_active_session(touch=False)
        session = self._active_session()
        if session.is_empty:
            QMessageBox.information(
                self, "导出会话", "当前会话还没有内容，没有可导出的记录。"
            )
            return
        suggested = default_export_dir() / default_export_name(session)
        self._export_markdown(
            suggested,
            lambda: session_to_markdown(session),
            f"已导出会话「{session.title}」",
        )

    @pyqtSlot()
    def _on_export_all(self) -> None:
        """把所有有内容的会话导出到同一个文件。"""
        self._save_active_session(touch=False)
        sessions = [session for session in self._sessions if not session.is_empty]
        if not sessions:
            QMessageBox.information(
                self, "导出会话", "还没有任何有内容的会话，没有可导出的记录。"
            )
            return
        suggested = default_export_dir() / default_bulk_export_name()
        self._export_markdown(
            suggested,
            lambda: sessions_to_markdown(sessions),
            f"已导出 {len(sessions)} 个会话",
        )

    def _export_markdown(self, suggested: Path, build, done_message: str) -> None:
        """弹保存框、写文件、报结果；三处导出共用这一段。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 Markdown", str(suggested), "Markdown 文件 (*.md)"
        )
        if not path:
            return
        try:
            written = write_markdown(Path(path), build())
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写入文件失败：{exc}")
            return
        self.statusBar().showMessage(f"{done_message} → {written}")

    def _confirm_delete(self, session: Session) -> bool:
        """删掉就找不回来，先问一次；单独成方法方便测试替换。"""
        answer = QMessageBox.question(
            self,
            "删除会话",
            f"确定删除会话「{session.title}」吗？\n这一轮的对话记录无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

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

        # 关窗前把当前会话存下来，下次启动能接着看
        self._prompt_input.clear_approval()
        self._save_active_session()
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
        # 立刻存档：标题跟着第一条消息定下来，任务中途崩掉也不至于丢掉提问
        self._save_active_session()
        self._files_changed = False
        self._prompt_input.clear()
        self._prompt_input.set_running(True)
        self.statusBar().showMessage(f"正在调用 {config.name}（{mode}）…")

        bridge = AgentBridge(self)
        bridge.agent_event.connect(self._on_agent_event)
        # 审批请求从工作线程发出，队列投递回界面线程铺确认卡
        bridge.approval_requested.connect(self._on_approval_requested)
        bridge.approval_expired.connect(self._on_approval_expired)
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
        # 在会话列表里标出正在跑的是哪一轮
        self._session_list.set_running(self._active_session_id)

    # ---- 审批 ----
    @pyqtSlot(object)
    def _on_approval_requested(self, request: ApprovalRequest) -> None:
        """把待确认的调用摆到输入区，任务线程继续在后台等着。"""
        # 任务在后台跑，用户可能没盯着屏幕；卡片出现前先响一声
        sound.play_confirmation()
        self._prompt_input.show_approval(request)
        self._fit_input_pane()
        self.statusBar().showMessage(f"等待你确认 {request.tool}…")

    @pyqtSlot(str, object)
    def _on_approval_resolved(self, request_id: str, decision: ApprovalDecision) -> None:
        """用户在确认卡上点了「允许执行」或「拒绝」。"""
        if self._bridge is not None:
            self._bridge.resolve(request_id, decision)
        self._restore_input_pane()
        self.statusBar().showMessage("已允许执行" if decision.allowed else "已拒绝该操作")

    @pyqtSlot(str, str)
    def _on_approval_expired(self, request_id: str, reason: str) -> None:
        """请求已失效（超时或任务被中止）：收起卡片，别让界面停在旧请求上。"""
        self._prompt_input.clear_approval(request_id)
        self._restore_input_pane()
        self.statusBar().showMessage(reason)
        if reason == "等待确认超时":
            self._chat_view.append_system("等待确认超时，已按拒绝处理。")

    def _fit_input_pane(self) -> None:
        """让输入区刚好容下确认卡：展开 / 收起参数时按新高度重排一次。"""
        sizes = self._main_splitter.sizes()
        if len(sizes) != 2:
            return
        if self._input_sizes is None:
            self._input_sizes = sizes
        total = sum(sizes)
        self._prompt_input.updateGeometry()
        # 只加确认卡本身的高度，输入框仍是用户拖出来的大小
        base = self._input_sizes[1]
        target = base + self._prompt_input.approval_height()
        # 无论如何给对话区留一点位置，否则确认卡会把它挤没
        target = min(target, max(140, total - 200))
        target = max(target, 140)
        self._main_splitter.setSizes([max(total - target, 120), target])

    def _restore_input_pane(self) -> None:
        """确认结束后把上下分栏还给用户原来的比例。"""
        if self._input_sizes is None:
            return
        sizes = self._input_sizes
        self._input_sizes = None
        self._main_splitter.setSizes(sizes)

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
        self._save_active_session()
        self._finish_task("就绪")
        # 提示音：任务跑完时提醒你回来看结果
        sound.play_completion()

    def _on_run_failed(self, message: str, handle) -> None:
        self._chat_view.append_system(f"错误：{message}")
        self._refresh_tree_if_changed()
        self._save_active_session()
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
        # 正卡在确认上的话，先按拒绝放行，否则线程要一直等到超时才动
        request_id = self._prompt_input.pending_request_id()
        if request_id and self._bridge is not None:
            self._bridge.resolve(
                request_id, ApprovalDecision(allowed=False, reason="用户停止了任务")
            )
            self._prompt_input.clear_approval(request_id)
            self._restore_input_pane()
        self._runtime.cancel()
        self.statusBar().showMessage("已请求停止，当前步骤结束后生效…")

    def _finish_task(self, status: str) -> None:
        """只复位界面状态；句柄要等线程真正结束再由 _on_thread_finished 释放。"""
        self._prompt_input.clear_approval()
        self._restore_input_pane()
        self._prompt_input.set_running(False)
        self._session_list.set_running(None)
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
        # 内容清空了，标题也要跟着回到默认值
        session = self._active_session()
        session.title = DEFAULT_TITLE
        session.entries = []
        session.set_history([])
        self._sync_session_list()
        self._session_store.save(self._sessions, self._active_session_id)

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
