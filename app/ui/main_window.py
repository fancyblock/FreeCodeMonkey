"""主窗口：串联配置、Provider 管理与 LangChain 调用。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
    QVBoxLayout,
)

from app.config import Settings
from app.constants import APP_NAME, APP_VERSION
from app.core import ProviderManager, get_spec
from app.ui.dialogs import SettingsDialog
from app.ui.widgets import ChatView, PromptInput
from app.workers import run_in_thread
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, settings: Settings, manager: ProviderManager) -> None:
        super().__init__()
        self._settings = settings
        self._manager = manager
        # 保持任务句柄的引用，防止被垃圾回收导致线程提前销毁
        self._active_handles: list = []
        # 会话内的多轮对话历史（不持久化）
        self._history: list[BaseMessage] = []

        self._chat_view = ChatView()
        self._prompt_input = PromptInput()
        self._provider_combo = QComboBox()

        self._build_ui()
        self._create_actions()
        self._create_menus()
        self._connect_signals()
        self._refresh_providers()
        self._restore_state()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(980, 680)

        # 顶部 Provider 选择栏
        provider_bar = QHBoxLayout()
        provider_bar.addWidget(QLabel("使用模型："))
        provider_bar.addWidget(self._provider_combo, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._chat_view)
        splitter.addWidget(self._prompt_input)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(provider_bar)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self.statusBar().showMessage("就绪")

    def _create_actions(self) -> None:
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
        file_menu.addAction(self._quit_action)

        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self._clear_action)
        edit_menu.addAction(self._settings_action)

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self._about_action)

    def _connect_signals(self) -> None:
        self._prompt_input.submitted.connect(self._on_submit)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)

    # ---- Provider 选择 ----
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
            # 无有效选中项时回落到第一个，并写回管理器
            self._provider_combo.setCurrentIndex(0)
            first_id = self._provider_combo.itemData(0)
            if first_id is not None:
                self._manager.active_id = str(first_id)
        self._provider_combo.blockSignals(False)

        has_provider = len(configs) > 0
        self._prompt_input.setDisabled(not has_provider)
        if has_provider:
            self.statusBar().showMessage("就绪")
        else:
            self.statusBar().showMessage("请先在「编辑 → 设置」中添加 LLM Provider")

    @pyqtSlot(int)
    def _on_provider_changed(self, index: int) -> None:
        provider_id = self._provider_combo.itemData(index)
        if provider_id:
            self._manager.active_id = str(provider_id)

    # ---- 状态持久化 ----
    def _restore_state(self) -> None:
        geometry = self._settings.geometry
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._settings.geometry = self.saveGeometry()
        self._settings.sync()
        super().closeEvent(event)

    # ---- 槽函数 ----
    @pyqtSlot(str)
    def _on_submit(self, text: str) -> None:
        """提交任务：显示用户消息，构造模型，后台异步生成。"""
        config = self._manager.active_config
        if config is None:
            self._chat_view.append_system("请先添加并选择一个 LLM Provider。")
            return

        self._chat_view.append_message("user", text)
        self._prompt_input.clear()
        self._prompt_input.set_busy(True)
        self.statusBar().showMessage(f"正在调用 {config.name} …")

        try:
            model = self._manager.build_model(config)
        except ValueError as exc:
            self._chat_view.append_system(f"配置错误：{exc}")
            self._prompt_input.set_busy(False)
            self.statusBar().showMessage("配置错误")
            return

        # 构造本轮消息：历史 + 当前输入
        messages = self._history + [HumanMessage(content=text)]
        handle = run_in_thread(model.invoke, messages)
        self._active_handles.append(handle)
        handle.worker.result.connect(
            lambda reply: self._on_result(reply, handle, messages)
        )
        handle.worker.failed.connect(lambda msg: self._on_error(msg, handle))

    def _on_result(self, reply: AIMessage, handle, messages: list[BaseMessage]) -> None:
        content = reply.content
        if isinstance(content, list):
            text_parts = [
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            ]
            content = "".join(text_parts)
        content = str(content)

        # 仅成功时把本轮消息写入历史
        self._history = messages + [AIMessage(content=content)]
        self._chat_view.append_message("assistant", content)
        self._finish_task(handle, "就绪")

    def _on_error(self, message: str, handle) -> None:
        self._chat_view.append_system(f"错误：{message}")
        self._finish_task(handle, "出错")

    def _finish_task(self, handle, status: str) -> None:
        self._prompt_input.set_busy(False)
        self.statusBar().showMessage(status)
        if handle in self._active_handles:
            self._active_handles.remove(handle)

    def _clear_history(self) -> None:
        self._history.clear()
        self._chat_view.clear_history()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._manager, self)
        dialog.exec()
        # 设置可能在对话框中被增删，刷新下拉框
        self._refresh_providers()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "简单的编程智能体，基于 LangChain，支持多种 LLM。<br>"
            "在「编辑 → 设置」中添加并管理模型连接。",
        )
