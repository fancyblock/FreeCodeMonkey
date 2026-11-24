"""左侧文件树：按需读取目录，双击文件发出预览请求。

目录内容只在第一次展开时读盘，启动开销与仓库大小无关。忽略规则复用
agent 那一套 ``SKIP_DIRS``，避免界面上看得到的目录和模型看得到的目录不一致。
这里不做任何写操作——改文件仍然只走 agent 的审批通道。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.agent.tools import SKIP_DIRS
from app.ui.icons import icon

# 单个目录最多渲染多少条；病态目录不应该让界面卡住
MAX_TREE_ENTRIES = 500

_PATH_ROLE = Qt.ItemDataRole.UserRole
# 目录的子项是否已经读过盘
_LOADED_ROLE = Qt.ItemDataRole.UserRole + 1


class FileTree(QWidget):
    """工作区文件树，懒加载，双击文件请求预览。"""

    file_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._tree = QTreeWidget()
        self._build_ui()
        self._connect_signals()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        title = QLabel("文件")
        title.setProperty("role", "section")

        self._refresh_button = QPushButton()
        self._refresh_button.setObjectName("icon")
        self._refresh_button.setIcon(icon("refresh", size=14))
        self._refresh_button.setFixedSize(24, 24)
        self._refresh_button.setToolTip("重新读取文件树")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._refresh_button)

        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(140)
        # 默认缩进 20，深几层就把窄列吃光了
        self._tree.setIndentation(14)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setUniformRowHeights(True)
        self._tree.setAnimated(False)
        # 这里只读：不允许改名、拖拽或拖放
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header)
        layout.addWidget(self._tree, 1)

    def _connect_signals(self) -> None:
        self._refresh_button.clicked.connect(self.refresh)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        # 用 activated 而不是 doubleClicked：双击和回车都能打开，且不会重复触发
        self._tree.itemActivated.connect(self._on_item_activated)

    # ---- 对外接口 ----
    def set_root(self, root: Path) -> None:
        """换一个工作区根目录并重建；目录不可用时只显示一行提示。"""
        self._root = Path(root)
        self._rebuild(expanded=set())

    def refresh(self) -> None:
        """重读目录，并尽量恢复到刷新前的展开状态。"""
        self._rebuild(expanded=self._expanded_paths())

    def root_item(self) -> QTreeWidgetItem | None:
        """返回根节点，供测试与调试使用。"""
        return self._tree.topLevelItem(0) if self._tree.topLevelItemCount() else None

    def is_loaded(self, item: QTreeWidgetItem) -> bool:
        """该目录的子项是否已经读过盘。"""
        return bool(item.data(0, _LOADED_ROLE))

    # ---- 重建 ----
    def _rebuild(self, expanded: set[str]) -> None:
        self._tree.clear()
        root = self._root
        if root is None:
            return
        if not root.is_dir():
            broken = QTreeWidgetItem([f"（工作区不可用：{root}）"])
            broken.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(broken)
            return

        top = self._make_dir_item(root.name or str(root), root)
        self._tree.addTopLevelItem(top)
        # 顶层总是展开，用户一进来就能看到根目录的内容
        top.setExpanded(True)

        # 浅的先展开：深的节点要等父层回填出子项之后才找得到
        for rel in sorted(expanded, key=lambda value: value.count("/")):
            item = self._find(rel)
            if item is not None:
                item.setExpanded(True)

    def _make_dir_item(self, name: str, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, _PATH_ROLE, str(path))
        item.setData(0, _LOADED_ROLE, False)
        item.setToolTip(0, str(path))
        # 先摆出展开箭头，不去 iterdir 探测是否为空
        item.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
        )
        placeholder = QTreeWidgetItem([""])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        item.addChild(placeholder)
        return item

    # ---- 懒加载 ----
    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if self.is_loaded(item):
            return
        item.setData(0, _LOADED_ROLE, True)
        item.takeChildren()  # 丢掉占位子项

        path = self._path_of(item)
        if path is None:
            return
        try:
            entries = sorted(
                path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError as exc:
            item.addChild(self._notice(f"（无法读取：{exc}）"))
            self._hide_indicator(item)
            return

        shown = 0
        hidden = 0
        for entry in entries:
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                if shown >= MAX_TREE_ENTRIES:
                    hidden += 1
                    continue
                shown += 1
                item.addChild(self._make_dir_item(view_name(entry, is_dir=True), entry))
            else:
                if shown >= MAX_TREE_ENTRIES:
                    hidden += 1
                    continue
                shown += 1
                item.addChild(self._make_file_item(entry))

        if hidden:
            item.addChild(self._notice(f"… 还有 {hidden} 项未显示"))
        if shown == 0:
            self._hide_indicator(item)

    def _hide_indicator(self, item: QTreeWidgetItem) -> None:
        item.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless
        )

    @staticmethod
    def _notice(text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        return item

    @staticmethod
    def _make_file_item(path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([view_name(path, is_dir=False)])
        item.setData(0, _PATH_ROLE, str(path))
        item.setToolTip(0, str(path))
        return item

    # ---- 定位 ----
    def _path_of(self, item: QTreeWidgetItem) -> Path | None:
        raw = item.data(0, _PATH_ROLE)
        return Path(str(raw)) if raw else None

    def _find(self, rel: str) -> QTreeWidgetItem | None:
        """按工作区相对路径找节点；途中展开父层以触发懒加载。"""
        top = self.root_item()
        if top is None:
            return None
        item = top
        for part in Path(rel).parts:
            item.setExpanded(True)
            match: QTreeWidgetItem | None = None
            for index in range(item.childCount()):
                child = item.child(index)
                child_path = self._path_of(child)
                if child_path is not None and child_path.name == part:
                    match = child
                    break
            if match is None:
                return None
            item = match
        return item

    def _expanded_paths(self) -> set[str]:
        root = self._root
        found: set[str] = set()
        if root is None:
            return found

        def walk(item: QTreeWidgetItem) -> None:
            for index in range(item.childCount()):
                child = item.child(index)
                if not child.isExpanded():
                    continue
                path = self._path_of(child)
                if path is not None:
                    try:
                        found.add(path.relative_to(root).as_posix())
                    except ValueError:
                        pass
                walk(child)

        for index in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(index))
        return found

    # ---- 交互 ----
    def _on_item_activated(self, item: QTreeWidgetItem, column: int) -> None:
        root = self._root
        path = self._path_of(item)
        if root is None or path is None or path.is_dir():
            return
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            return
        self.file_activated.emit(rel)


def view_name(path: Path, *, is_dir: bool) -> str:
    """条目显示名：目录补斜杠，没有图标时用来和信息一起区分。"""
    return f"{path.name}/" if is_dir else path.name
