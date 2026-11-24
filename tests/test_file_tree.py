"""文件树与预览弹窗测试：不需要模型，offscreen 运行。

运行：.venv\\Scripts\\python.exe tests\\test_file_tree.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QTreeWidgetItem

from app.agent import events as E
from app.agent.events import AgentEvent
from app.config import Settings
from app.core import ProviderManager
from app.ui import MainWindow
from app.ui.dialogs import FilePreviewDialog
from app.ui.dialogs.file_preview_dialog import MAX_PREVIEW_LINES, read_preview
from app.ui.sessions import SessionStore
from app.ui.widgets import FileTree
from app.ui.widgets.file_tree import MAX_TREE_ENTRIES


def names(item: QTreeWidgetItem) -> list[str]:
    return [item.child(index).text(0) for index in range(item.childCount())]


def child_named(item: QTreeWidgetItem, text: str) -> QTreeWidgetItem:
    for index in range(item.childCount()):
        child = item.child(index)
        if child.text(0) == text:
            return child
    raise AssertionError(f"没有名为 {text!r} 的子项，实际有 {names(item)}")


class FileTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "empty").mkdir()
        (self.root / ".git").mkdir()
        (self.root / "node_modules").mkdir()
        (self.root / "zeta.txt").write_text("z\n", encoding="utf-8")
        (self.root / "Alpha.txt").write_text("a\n", encoding="utf-8")
        self.tree = FileTree()
        self.tree.set_root(self.root)

    def tearDown(self) -> None:
        self.tree.close()
        self._tmp.cleanup()

    # ---- 装配 ----
    def test_root_children_sorted_and_filtered(self) -> None:
        top = self.tree.root_item()
        assert top is not None
        self.assertTrue(top.isExpanded())
        # 目录在前，各自不区分大小写；忽略名单里的目录不出现
        self.assertEqual(
            names(top), ["empty/", "pkg/", "Alpha.txt", "zeta.txt"]
        )

    # ---- 懒加载 ----
    def test_children_are_read_lazily(self) -> None:
        top = self.tree.root_item()
        assert top is not None
        pkg = child_named(top, "pkg/")
        # 未展开：只有一个占位子项，且没读过盘
        self.assertFalse(self.tree.is_loaded(pkg))
        self.assertEqual(pkg.childCount(), 1)
        self.assertEqual(pkg.child(0).text(0), "")

        pkg.setExpanded(True)

        self.assertTrue(self.tree.is_loaded(pkg))
        self.assertEqual(names(pkg), ["mod.py"])

    def test_empty_directory_loses_placeholder(self) -> None:
        top = self.tree.root_item()
        assert top is not None
        empty = child_named(top, "empty/")

        empty.setExpanded(True)

        self.assertEqual(empty.childCount(), 0)
        self.assertTrue(self.tree.is_loaded(empty))

    def test_directory_entry_cap(self) -> None:
        crowded = self.root / "crowded"
        crowded.mkdir()
        for index in range(MAX_TREE_ENTRIES + 20):
            (crowded / f"f{index:04d}.txt").write_text("", encoding="utf-8")
        self.tree.refresh()

        top = self.tree.root_item()
        assert top is not None
        item = child_named(top, "crowded/")
        item.setExpanded(True)

        # 上限条 + 一条截断提示
        self.assertEqual(item.childCount(), MAX_TREE_ENTRIES + 1)
        self.assertIn("还有 20 项未显示", item.child(MAX_TREE_ENTRIES).text(0))

    # ---- 重建 ----
    def test_refresh_keeps_expansion(self) -> None:
        top = self.tree.root_item()
        assert top is not None
        child_named(top, "pkg/").setExpanded(True)
        (self.root / "pkg" / "fresh.py").write_text("y = 2\n", encoding="utf-8")

        self.tree.refresh()

        top = self.tree.root_item()
        assert top is not None
        pkg = child_named(top, "pkg/")
        self.assertTrue(pkg.isExpanded())
        self.assertEqual(names(pkg), ["fresh.py", "mod.py"])

    def test_set_root_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            Path(other, "only-here.md").write_text("m\n", encoding="utf-8")

            self.tree.set_root(Path(other))

            top = self.tree.root_item()
            assert top is not None
            self.assertEqual(names(top), ["only-here.md"])

    def test_missing_root_shows_notice(self) -> None:
        gone = self.root / "does-not-exist"
        self.tree.set_root(gone)

        top = self.tree.root_item()
        assert top is not None
        self.assertIn("工作区不可用", top.text(0))
        self.assertEqual(top.childCount(), 0)

    # ---- 交互 ----
    def test_activating_file_emits_relative_path(self) -> None:
        seen: list[str] = []
        self.tree.file_activated.connect(seen.append)
        top = self.tree.root_item()
        assert top is not None
        item = child_named(top, "Alpha.txt")

        self.tree._tree.itemActivated.emit(item, 0)

        self.assertEqual(seen, ["Alpha.txt"])

    def test_activating_nested_file_uses_posix_path(self) -> None:
        seen: list[str] = []
        self.tree.file_activated.connect(seen.append)
        top = self.tree.root_item()
        assert top is not None
        pkg = child_named(top, "pkg/")
        pkg.setExpanded(True)

        self.tree._tree.itemActivated.emit(child_named(pkg, "mod.py"), 0)

        self.assertEqual(seen, ["pkg/mod.py"])

    def test_activating_directory_emits_nothing(self) -> None:
        seen: list[str] = []
        self.tree.file_activated.connect(seen.append)
        top = self.tree.root_item()
        assert top is not None

        self.tree._tree.itemActivated.emit(child_named(top, "pkg/"), 0)

        self.assertEqual(seen, [])


class FilePreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_line_numbers_match_read_file(self) -> None:
        (self.root / "sample.py").write_text("alpha\nbeta\n", encoding="utf-8")
        caption = QLabel()

        body = read_preview(self.root, "sample.py", caption)

        self.assertEqual(body.splitlines()[0], "    1\talpha")
        self.assertEqual(body.splitlines()[1], "    2\tbeta")
        self.assertIn("共 2 行", caption.text())

    def test_rejects_path_escape(self) -> None:
        outside = Path(self._tmp.name).parent / "outside-secret.txt"
        body = read_preview(self.root, "../outside-secret.txt")

        self.assertNotIn("outside-secret", body.replace("../outside-secret.txt", ""))
        self.assertIn("超出工作区范围", body)

    def test_binary_file_is_reported(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02hello")

        body = read_preview(self.root, "blob.bin")

        self.assertIn("不是 UTF-8 文本", body)

    def test_large_file_is_clipped(self) -> None:
        (self.root / "many.txt").write_text(
            "\n".join(f"line {index}" for index in range(MAX_PREVIEW_LINES + 50)),
            encoding="utf-8",
        )

        body = read_preview(self.root, "many.txt")

        self.assertEqual(len(body.splitlines()), MAX_PREVIEW_LINES)

    def test_missing_file_is_reported(self) -> None:
        body = read_preview(self.root, "nope.txt")

        self.assertIn("路径不存在", body)

    def test_dialog_renders_body(self) -> None:
        (self.root / "hello.py").write_text('print("hi")\n', encoding="utf-8")

        dialog = FilePreviewDialog(self.root, "hello.py")
        try:
            self.assertIn('    1\tprint("hi")', dialog._body.toPlainText())
        finally:
            dialog.close()


class StubPreviewDialog:
    """替掉模态预览窗，记录被要求打开哪个文件。"""

    opened: list[tuple[str, str]] = []

    def __init__(self, root, rel_path, parent=None) -> None:
        self.root = root
        self.rel_path = rel_path
        StubPreviewDialog.opened.append((str(root), rel_path))

    def exec(self) -> int:
        return 0


class MainWindowFileTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import app.ui.main_window as window_module

        self._original_preview = window_module.FilePreviewDialog
        window_module.FilePreviewDialog = StubPreviewDialog
        StubPreviewDialog.opened = []

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text('print("hi")\n', encoding="utf-8")

        settings = Settings(
            QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        )
        settings.workspace_root = str(self.root)
        # 会话存到临时目录，别去碰真实用户数据
        store = SessionStore(self.root / "sessions.json")
        self.window = MainWindow(settings, ProviderManager(settings), store)

    def tearDown(self) -> None:
        import app.ui.main_window as window_module

        window_module.FilePreviewDialog = self._original_preview
        self.window.close()
        self._tmp.cleanup()

    def test_left_column_is_vertical_splitter(self) -> None:
        self.assertEqual(
            self.window._left_splitter.orientation(), Qt.Orientation.Vertical
        )
        # 从上到下：文件树 / 会话 / 提示词
        self.assertIs(self.window._left_splitter.widget(0), self.window._file_tree)
        self.assertIs(self.window._left_splitter.widget(1), self.window._session_list)
        self.assertIs(self.window._left_splitter.widget(2), self.window._prompt_list)
        self.assertIs(
            self.window._body_splitter.widget(0), self.window._left_splitter
        )

    def test_tree_shows_workspace_on_startup(self) -> None:
        top = self.window._file_tree.root_item()
        assert top is not None
        self.assertIn("src/", names(top))

    def test_activation_opens_preview(self) -> None:
        self.window._file_tree.file_activated.emit("src/app.py")

        self.assertEqual(len(StubPreviewDialog.opened), 1)
        _, rel_path = StubPreviewDialog.opened[0]
        self.assertEqual(rel_path, "src/app.py")

    def test_choose_workspace_rebuilds_tree(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            Path(other, "only-here.md").write_text("m\n", encoding="utf-8")
            from unittest.mock import patch

            with patch(
                "app.ui.main_window.QFileDialog.getExistingDirectory",
                return_value=other,
            ):
                self.window._choose_workspace()

        top = self.window._file_tree.root_item()
        assert top is not None
        self.assertEqual(names(top), ["only-here.md"])

    def test_edit_marks_tree_dirty_and_refreshes_at_run_end(self) -> None:
        (self.root / "created.py").write_text("x = 2\n", encoding="utf-8")

        self.window._on_agent_event(
            AgentEvent(kind=E.TOOL_RESULT, tool="edit_file", ok=True, text="已修改")
        )

        # 任务还没结束，树保持原样
        self.assertTrue(self.window._files_changed)
        top = self.window._file_tree.root_item()
        assert top is not None
        self.assertNotIn("created.py", names(top))

        self.window._on_run_done([], None)

        top = self.window._file_tree.root_item()
        assert top is not None
        self.assertIn("created.py", names(top))
        self.assertFalse(self.window._files_changed)

    def test_read_only_tool_does_not_refresh_tree(self) -> None:
        (self.root / "created.py").write_text("x = 2\n", encoding="utf-8")

        self.window._on_agent_event(
            AgentEvent(kind=E.TOOL_RESULT, tool="read_file", ok=True, text="内容")
        )
        self.window._on_run_done([], None)

        top = self.window._file_tree.root_item()
        assert top is not None
        self.assertNotIn("created.py", names(top))


if __name__ == "__main__":
    unittest.main(verbosity=2)
