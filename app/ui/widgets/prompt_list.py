"""左侧提示词列表：按提交顺序记录本会话的用户输入。

条目只负责展示与转发点击；「跳转到对话区哪一行」由 ChatView 的标记决定，
这里不关心文档结构，避免界面组件互相知道对方的实现细节。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

_PREVIEW_CHARS = 22


class PromptList(QWidget):
    """提示词列表 + 标题，点击条目发出对应的定位标记。"""

    prompt_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        title = QLabel("提示词")
        title.setStyleSheet("color:#5f6368;font-weight:600;padding:2px 2px 4px 2px;")

        # 宽度交给外层分栏决定：用户拉宽左列时这里应该跟着变宽
        self._list.setMinimumWidth(140)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(title)
        layout.addWidget(self._list, 1)

    def _connect_signals(self) -> None:
        self._list.itemClicked.connect(self._emit_mark)
        self._list.itemActivated.connect(self._emit_mark)

    def _emit_mark(self, item: QListWidgetItem) -> None:
        mark = item.data(Qt.ItemDataRole.UserRole)
        if mark:
            self.prompt_activated.emit(str(mark))

    # ---- 对外接口 ----
    def add_prompt(self, text: str, mark: str) -> None:
        """登记一条提示词；text 用全文，列表里显示折叠后的单行预览。"""
        preview = " ".join(text.split())
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS] + "…"
        item = QListWidgetItem(f"#{self._list.count() + 1}  {preview}")
        item.setData(Qt.ItemDataRole.UserRole, mark)
        item.setToolTip(text)
        self._list.addItem(item)
        self._list.setCurrentItem(item)
        self._list.scrollToBottom()

    def clear_prompts(self) -> None:
        self._list.clear()

    def count(self) -> int:
        return self._list.count()

    def item_mark(self, index: int) -> str | None:
        """读取指定条目的定位标记，供测试与调试使用。"""
        item = self._list.item(index)
        if item is None:
            return None
        mark = item.data(Qt.ItemDataRole.UserRole)
        return str(mark) if mark else None
