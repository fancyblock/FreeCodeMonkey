"""对话展示区：以气泡样式呈现用户与助手的消息。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QTextBrowser, QWidget


class ChatView(QTextBrowser):
    """只读的对话记录视图，提供按角色追加消息的接口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self.document().setDocumentMargin(12)
        self._apply_stylesheet()

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            """
            QTextBrowser {
                background: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                font-size: 13px;
                line-height: 150%;
            }
            """
        )

    def append_message(self, role: str, content: str) -> None:
        """追加一条消息。role 仅用于标识展示，内容按纯文本转义后显示。"""
        escaped = (
            content.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        is_user = role == "user"
        color = "#1a73e8" if is_user else "#2e7d32"
        label = "你" if is_user else "助手"
        self.append(
            f'<div style="margin:8px 0;">'
            f'<span style="color:{color};font-weight:600;">{label}:</span> '
            f'<span style="color:#202124;">{escaped}</span>'
            f"</div>"
        )
        self._scroll_to_bottom()

    def append_system(self, text: str) -> None:
        """追加一条系统提示（如错误、状态信息）。"""
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.append(
            f'<div style="margin:6px 0;color:#9aa0a6;font-style:italic;">'
            f"{escaped}</div>"
        )
        self._scroll_to_bottom()

    def clear_history(self) -> None:
        self.clear()

    def _scroll_to_bottom(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
