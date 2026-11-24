"""左侧会话列表：列出所有会话，双击切换到该会话，右键删除。

只负责展示与转发意图；「切过去的记录怎么重建」「删掉之后激活哪一个」由主窗口
决定，避免这个组件反过来依赖 ChatView 与会话存储的实现细节。
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import icon
from app.ui.theme import palette

# 保存原始标题与提示，切换「进行中」样式时据此还原，避免文字越叠越长
_TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
_TIP_ROLE = Qt.ItemDataRole.UserRole + 2

_RUNNING_HINT = "正在执行任务…"
# 8px 在窄栏里太不起眼，10px 既醒目又不至于把标题挤走
_DOT_SIZE = 10


class SessionList(QWidget):
    """会话列表 + 标题，双击条目发出会话 id。"""

    session_activated = pyqtSignal(str)
    session_delete_requested = pyqtSignal(str)
    export_current_requested = pyqtSignal()
    export_all_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        # 当前正在执行任务的会话；空串表示没有
        self._running_id = ""
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        title = QLabel("会话")
        title.setProperty("role", "section")

        self._list.setMinimumWidth(140)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        # 不开 setUniformItemSizes：它按首个条目的尺寸统一所有行，
        # 而「进行中」那项多一个圆点图标，会被按无图标的宽度裁掉。
        # 会话上限 100 条，逐条算尺寸的开销可以忽略。
        self._list.setUniformItemSizes(False)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setToolTip("双击切换会话，右键删除")
        self._list.setIconSize(QSize(_DOT_SIZE, _DOT_SIZE))

        # 左栏窄，两个长标签横排放不下，改成上下两行各占满宽度
        self._export_current_button = QPushButton("导出当前会话")
        self._export_current_button.setToolTip("把当前这一轮对话导出成 Markdown 文件")
        self._export_all_button = QPushButton("导出全部会话")
        self._export_all_button.setToolTip("把所有有内容的会话导出到同一个 Markdown 文件")

        exports = QVBoxLayout()
        exports.setContentsMargins(0, 0, 0, 0)
        exports.setSpacing(4)
        exports.addWidget(self._export_current_button)
        exports.addWidget(self._export_all_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(title)
        layout.addWidget(self._list, 1)
        layout.addLayout(exports)

    def _connect_signals(self) -> None:
        self._list.itemDoubleClicked.connect(self._emit_activated)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._open_context_menu)
        self._export_current_button.clicked.connect(self.export_current_requested.emit)
        self._export_all_button.clicked.connect(self.export_all_requested.emit)

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
                item.setData(_TITLE_ROLE, title)
                item.setData(_TIP_ROLE, tip or "")
                if tip:
                    item.setToolTip(tip)
                self._list.addItem(item)
            # 列表重建后要把「进行中」标回原来那一项
            self._refresh_items()
        finally:
            self._list.blockSignals(False)

    def set_running(self, session_id: str | None) -> None:
        """标记正在执行任务的会话；传 None 表示没有。"""
        self._running_id = session_id or ""
        self._refresh_items()

    def running_id(self) -> str:
        """当前标着「进行中」的会话 id；没有则为空串。"""
        return self._running_id

    def _refresh_items(self) -> None:
        for index in range(self._list.count()):
            self._style_item(self._list.item(index))

    def _style_item(self, item: QListWidgetItem) -> None:
        """按是否进行中切换条目样式。

        用「圆点图标 + 粗体」而不是设置文字颜色：全局 QSS 的
        ``QListWidget::item:selected { color: ... }`` 会覆盖条目前景色，
        而图标和字体不受样式表影响，能稳定显示出来。
        """
        title = str(item.data(_TITLE_ROLE) or item.text())
        tip = str(item.data(_TIP_ROLE) or "")
        running = bool(self._running_id) and (
            item.data(Qt.ItemDataRole.UserRole) == self._running_id
        )

        item.setText(title)
        if running:
            item.setIcon(icon("dot", palette().accent, _DOT_SIZE))
            font = self._list.font()
            font.setBold(True)
            item.setFont(font)
            item.setToolTip(f"{tip}\n{_RUNNING_HINT}" if tip else _RUNNING_HINT)
        else:
            item.setIcon(QIcon())
            item.setFont(self._list.font())
            item.setToolTip(tip)

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
