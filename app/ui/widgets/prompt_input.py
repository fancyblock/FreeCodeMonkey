"""输入区：多行文本输入 + 发送 / 停止按钮。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class _PromptEdit(QPlainTextEdit):
    """支持 Ctrl+Enter 提交的文本编辑器。"""

    submit_shortcut = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit_shortcut.emit()
            return
        super().keyPressEvent(event)


class PromptInput(QWidget):
    """提示词输入组件。"""

    submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = _PromptEdit()
        self._send_button = QPushButton("发送 (Ctrl+Enter)")
        self._stop_button = QPushButton("停止")
        self._stop_button.setVisible(False)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        self._edit.setPlaceholderText("描述你想让 agent 完成的编程任务…")
        self._edit.setMinimumHeight(80)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._stop_button)
        buttons.addWidget(self._send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)
        layout.addLayout(buttons)

    def _connect_signals(self) -> None:
        self._send_button.clicked.connect(self._emit_text)
        self._stop_button.clicked.connect(self.stop_requested.emit)
        self._edit.submit_shortcut.connect(self._emit_text)

    def _emit_text(self) -> None:
        text = self.to_plain_text()
        if text:
            self.submitted.emit(text)

    def to_plain_text(self) -> str:
        return self._edit.toPlainText().strip()

    def clear(self) -> None:
        self._edit.clear()

    def set_running(self, running: bool) -> None:
        """任务进行中：禁用输入与发送，显示停止按钮。"""
        self._edit.setDisabled(running)
        self._send_button.setDisabled(running)
        self._stop_button.setVisible(running)
        if not running:
            self._edit.setFocus()
