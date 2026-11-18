"""输入区：多行文本输入 + 发送按钮。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = _PromptEdit()
        self._send_button = QPushButton("发送 (Ctrl+Enter)")
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        self._edit.setPlaceholderText("输入你的编程任务或问题…")
        self._edit.setMinimumHeight(80)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)
        layout.addWidget(self._send_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _connect_signals(self) -> None:
        self._send_button.clicked.connect(self._emit_text)
        self._edit.submit_shortcut.connect(self._emit_text)

    def _emit_text(self) -> None:
        text = self.to_plain_text()
        if text.strip():
            self.submitted.emit(text)

    def to_plain_text(self) -> str:
        return self._edit.toPlainText().strip()

    def clear(self) -> None:
        self._edit.clear()

    def set_busy(self, busy: bool) -> None:
        """忙碌时禁用输入，避免重复提交。"""
        self._edit.setDisabled(busy)
        self._send_button.setDisabled(busy)
