"""对话展示区：用户/助手消息气泡 + agent 执行步骤流。

步骤行与消息气泡视觉上要能区分：消息是「结果」，步骤是「过程」。
"""

from __future__ import annotations

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QTextBrowser, QWidget

_TONES = {
    "muted": "#5f6368",
    "info": "#1a73e8",
    "ok": "#1e8e3e",
    "bad": "#c5221f",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _block(text: str, max_lines: int = 8, max_cols: int = 180) -> str:
    """截断并转义一段等宽文本，避免长输出把视图撑爆。"""
    lines = text.splitlines()
    shown = [line[:max_cols] for line in lines[:max_lines]]
    if len(lines) > max_lines:
        shown.append(f"...（还有 {len(lines) - max_lines} 行）")
    return _escape("\n".join(shown))


class ChatView(QTextBrowser):
    """只读的对话记录视图。"""

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

    # ---- 消息 ----
    def append_message(self, role: str, content: str) -> None:
        """追加一条用户或助手消息，内容按纯文本转义后显示。"""
        escaped = _escape(content).replace("\n", "<br>")
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
        """追加一条系统提示（错误、状态信息）。"""
        escaped = _escape(text).replace("\n", "<br>")
        self.append(
            f'<div style="margin:6px 0;color:#9aa0a6;font-style:italic;">{escaped}</div>'
        )
        self._scroll_to_bottom()

    # ---- agent 步骤 ----
    def append_step(self, title: str, body: str = "", tone: str = "muted") -> None:
        """追加一行执行步骤。body 用等宽字体，最多展示 8 行。"""
        color = _TONES.get(tone, _TONES["muted"])
        html = ['<div style="margin:5px 0 5px 20px;font-size:12px;line-height:145%;">']
        if title:
            html.append(f'<span style="color:{color};">{_escape(title)}</span>')
        if body:
            html.append(
                '<pre style="margin:3px 0 0 10px;padding-left:8px;'
                "border-left:2px solid #e8eaed;font-family:Consolas,'Courier New',monospace;"
                f'font-size:11px;color:#80868b;">{_block(body)}</pre>'
            )
        html.append("</div>")
        self.append("".join(html))
        self._scroll_to_bottom()

    def append_divider(self) -> None:
        self.append('<hr style="border:0;border-top:1px solid #eceff1;margin:12px 0;">')
        self._scroll_to_bottom()

    def clear_history(self) -> None:
        self.clear()

    def _scroll_to_bottom(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
