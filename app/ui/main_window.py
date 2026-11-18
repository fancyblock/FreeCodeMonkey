"""主窗口：串联配置、LLM 抽象层与异步 Worker。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
    QVBoxLayout,
)

from app.config import Settings
from app.constants import APP_NAME, APP_VERSION
from app.core import LLMResponse, ProviderRegistry
from app.ui.dialogs import SettingsDialog
from app.ui.widgets import ChatView, PromptInput
from app.workers import run_in_thread


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        # 保持任务句柄的引用，防止被垃圾回收导致线程提前销毁
        self._active_handles: list = []

        self._chat_view = ChatView()
        self._prompt_input = PromptInput()

        self._build_ui()
        self._create_actions()
        self._create_menus()
        self._connect_signals()
        self._restore_state()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(980, 680)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._chat_view)
        splitter.addWidget(self._prompt_input)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self.statusBar().showMessage("就绪")

    def _create_actions(self) -> None:
        self._settings_action = QAction("设置…", self)
        self._settings_action.triggered.connect(self._open_settings)

        self._clear_action = QAction("清空对话", self)
        self._clear_action.triggered.connect(self._chat_view.clear_history)

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
        """提交任务：显示用户消息，创建 provider，后台异步生成。"""
        self._chat_view.append_message("user", text)
        self._prompt_input.clear()
        self._prompt_input.set_busy(True)
        self.statusBar().showMessage("正在生成…")

        try:
            provider = ProviderRegistry.create(
                self._settings.provider,
                api_key=self._settings.api_key,
                model=self._settings.model,
            )
        except ValueError as exc:
            self._chat_view.append_system(str(exc))
            self._prompt_input.set_busy(False)
            self.statusBar().showMessage("配置错误")
            return

        handle = run_in_thread(provider.generate, text)
        self._active_handles.append(handle)
        handle.worker.result.connect(lambda r: self._on_result(r, handle))
        handle.worker.failed.connect(lambda msg: self._on_error(msg, handle))

    def _on_result(self, response: LLMResponse, handle) -> None:
        self._chat_view.append_message("assistant", response.content)
        self._finish_task(handle, "就绪")

    def _on_error(self, message: str, handle) -> None:
        self._chat_view.append_system(f"错误：{message}")
        self._finish_task(handle, "出错")

    def _finish_task(self, handle, status: str) -> None:
        self._prompt_input.set_busy(False)
        self.statusBar().showMessage(status)
        if handle in self._active_handles:
            self._active_handles.remove(handle)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "简单的编程智能体，支持多种 LLM。<br>"
            "当前版本为基础架构骨架。",
        )
