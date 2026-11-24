"""左侧会话列表：列出所有会话，双击切换到该会话，右键删除。

只负责展示与转发意图；「切过去的记录怎么重建」「删掉之后激活哪一个」由主窗口
决定，避免这个组件反过来依赖 ChatView 与会话存储的实现细节。
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)


class SessionList(QWidget):
    """会话列表 + 标题，双击条目发出会话 id。"""

    session_activated = pyqtSignal(str)
    session_delete_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        title = QLabel("会话")
        title.setProperty("role", "section")

        self._list.setMinimumWidth(140)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setToolTip("双击切换会话，右键删除")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(title)
        layout.addWidget(self._list, 1)

    def _connect_signals(self) -> None:
        self._list.itemDoubleClicked.connect(self._emit_activated)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._open_context_menu)

    def _emit_activated(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id:
            self.session_activated.emit(str(session_id))

    def _open_context_menu(self, pos: QPoint) -> None:
        """右键菜单：只在真的点在某一项上时才弹。"""
        item = self._list.itemAt(pos)
        if item is None:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if not session_id:
            return
        # 右键不改变选中项：删除别的会话不应让当前高亮跳走
        menu = QMenu(self)
        delete_action = menu.addAction("删除会话")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is delete_action:
            self.session_delete_requested.emit(str(session_id))

    # ---- 对外接口 ----
    def set_sessions(self, sessions: list[tuple[str, str, str]]) -> None:
        """重建列表；每项是 (会话 id, 标题, 悬停提示)。"""
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for session_id, title, tip in sessions:
                item = QListWidgetItem(title)
                item.setData(Qt.ItemDataRole.UserRole, session_id)
                if tip:
                    item.setToolTip(tip)
                self._list.addItem(item)
        finally:
            self._list.blockSignals(False)

    def select(self, session_id: str) -> bool:
        """把某一项设为当前选中；找不到返回 False。"""
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == session_id:
                self._list.setCurrentItem(item)
                return True
        return False

    def current_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return str(data) if data else None

    def count(self) -> int:
        return self._list.count()
