"""对话展示区：用户/助手消息气泡 + agent 执行步骤流。

步骤行与消息气泡视觉上要能区分：消息是「结果」，步骤是「过程」。
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QTextBrowser, QTextEdit, QWidget

from app.ui.theme import palette

# HTML 片段里只能用字体栈字符串，QFont 下发不到富文本片段
_MONO_STACK = "Consolas,'Cascadia Mono','Courier New',monospace"

# 步骤的语义色调 → 色板字段名
_TONE_ATTRS = {
    "muted": "text_muted",
    "info": "accent",
    "ok": "success",
    "bad": "danger",
}


def _tone_color(tone: str) -> str:
    return getattr(palette(), _TONE_ATTRS.get(tone, "text_muted"))


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
        self.document().setDocumentMargin(16)
        # 空状态用 placeholder：不会写进文档，清空后 toPlainText() 仍为空
        self.setPlaceholderText("描述你想让 agent 完成的编程任务，按 Ctrl+Enter 发送…")
        # 每条消息在文档里的字符区间：标记名 → (起始位置, 结束位置)
        self._marks: dict[str, tuple[int, int]] = {}
        self._seq = 0
        # 写入过的内容记录，切换会话时据此重放
        self._entries: list[tuple] = []
        # 重放期间逐条滚到底很慢，先关掉，结束时统一滚一次
        self._suppress_scroll = False

    # ---- 消息 ----
    def append_message(self, role: str, content: str) -> str:
        """追加一条用户或助手消息，返回可用于定位的标记名。"""
        tokens = palette()
        escaped = _escape(content).replace("\n", "<br>")
        is_user = role == "user"
        label = "你" if is_user else "助手"
        head = (
            f'<span style="color:{tokens.accent if is_user else tokens.success};'
            f'font-weight:600;">{label}</span>'
            f'<br><span style="color:{tokens.text};">{escaped}</span>'
        )
        if is_user:
            # 富文本不支持 border-radius / border-left，用两格表格做出
            # 「浅底 + 左侧强调条」的引述块效果。强调条必须用 td 的
            # width/bgcolor 属性，写成 CSS 会被富文本引擎按内容宽度分配。
            body = (
                '<table width="100%" cellspacing="0" cellpadding="0"'
                ' style="margin:10px 0;">'
                f'<tr><td width="4" bgcolor="{tokens.accent}"></td>'
                f'<td bgcolor="{tokens.surface_alt}"'
                f' style="padding:7px 11px;line-height:150%;">{head}</td>'
                "</tr></table>"
            )
        else:
            body = (
                f'<div style="margin:10px 0;line-height:150%;">{head}</div>'
            )
        # append 会另起一个块；先记下插入点，事后据此定位新消息的首块
        start = self.document().characterCount() - 1
        self.append(body)
        block = self.document().findBlock(start)
        if block.next().isValid():
            block = block.next()
        name = f"msg-{self._seq}"
        self._seq += 1
        self._marks[name] = (block.position(), self.document().characterCount() - 1)
        self._entries.append(("msg", role, content))
        self.setExtraSelections([])
        self._scroll_to_bottom()
        return name

    def append_system(self, text: str) -> None:
        """追加一条系统提示（错误、状态信息）。"""
        escaped = _escape(text).replace("\n", "<br>")
        self.append(
            f'<div style="margin:6px 0;color:{palette().text_faint};'
            f'font-style:italic;">{escaped}</div>'
        )
        self._entries.append(("sys", text))
        self._scroll_to_bottom()

    # ---- agent 步骤 ----
    def append_step(self, title: str, body: str = "", tone: str = "muted") -> None:
        """追加一行执行步骤。body 用等宽字体，最多展示 8 行。"""
        tokens = palette()
        color = _tone_color(tone)
        html = ['<div style="margin:6px 0 6px 20px;font-size:12px;line-height:145%;">']
        if title:
            html.append(f'<span style="color:{color};">{_escape(title)}</span>')
        if body:
            html.append(
                '<pre style="margin:4px 0 0 10px;'
                f"font-family:{_MONO_STACK};font-size:11px;"
                f'color:{tokens.text_faint};">{_block(body)}</pre>'
            )
        html.append("</div>")
        self.append("".join(html))
        self._entries.append(("step", title, body, tone))
        self._scroll_to_bottom()

    def append_divider(self) -> None:
        # 不用 <hr>：富文本把它画成带上下边框的空 frame，会出来两条线。
        # 1px 字号 + 1px 行高的空段落是唯一能按令牌画出细线的写法。
        self.append(
            f'<p style="margin:14px 0;background-color:{palette().border};'
            'font-size:1px;line-height:1px;">&nbsp;</p>'
        )
        self._entries.append(("div",))
        self._scroll_to_bottom()

    # ---- 定位 ----
    def scroll_to_mark(self, name: str) -> bool:
        """把标记对应的消息滚到视口上方并高亮；标记不存在时返回 False。"""
        span = self._marks.get(name)
        if span is None:
            return False
        start, end = span
        block = self.document().findBlock(start)
        # 用文档坐标算目标位置：cursorRect 对视野外的位置会被夹到视口内，不可靠
        top = self.document().documentLayout().blockBoundingRect(block).top()
        bar = self.verticalScrollBar()
        # 落在视口上方 1/4 处，比贴顶更容易看清上下文
        bar.setValue(int(top) - self.viewport().height() // 4)
        self._highlight(start, end)
        return True

    def _highlight(self, start: int, end: int) -> None:
        selection = QTextEdit.ExtraSelection()
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(max(start + 1, end), QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(palette().highlight))
        selection.cursor = cursor
        selection.format = fmt
        self.setExtraSelections([selection])

    def clear_history(self) -> None:
        self.clear()
        self._marks.clear()
        self._entries.clear()
        self._seq = 0

    # ---- 会话切换 ----
    def entries(self) -> list[tuple]:
        """当前记录，供会话切换时存档。"""
        return list(self._entries)

    def replay(self, entries: list) -> list[tuple[str, str]]:
        """清空后按记录重放，返回 (用户提示词, 标记名) 列表。

        重放会把 _seq 归零，所以同一份记录每次生成的标记名都一样，
        提示词列表里存的标记在切回来之后仍然可用。
        """
        self.clear_history()
        self._suppress_scroll = True
        prompts: list[tuple[str, str]] = []
        try:
            for entry in entries:
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                kind = str(entry[0])
                if kind == "msg" and len(entry) >= 3:
                    role = str(entry[1])
                    content = "" if entry[2] is None else str(entry[2])
                    mark = self.append_message(role, content)
                    if role == "user":
                        prompts.append((content, mark))
                elif kind == "sys" and len(entry) >= 2:
                    self.append_system("" if entry[1] is None else str(entry[1]))
                elif kind == "step" and len(entry) >= 2:
                    body = "" if len(entry) < 3 or entry[2] is None else str(entry[2])
                    tone = "muted" if len(entry) < 4 or entry[3] is None else str(entry[3])
                    self.append_step(
                        "" if entry[1] is None else str(entry[1]), body, tone
                    )
                elif kind == "div":
                    self.append_divider()
        finally:
            self._suppress_scroll = False
        self._scroll_to_bottom()
        return prompts

    def _scroll_to_bottom(self) -> None:
        if self._suppress_scroll:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
