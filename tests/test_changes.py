"""一轮任务的文件改动记录：快照差异、行数明细、界面展示与导出。

运行：.venv\\Scripts\\python.exe tests\\test_changes.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage
from PyQt6.QtWidgets import QApplication

from app.agent import AgentRuntime, Workspace
from app.agent.changes import (
    DELETED,
    MODIFIED,
    NEW,
    FileChange,
    changed_files,
    snapshot,
)
from app.ui.export import session_to_markdown
from app.ui.sessions import Session
from app.ui.widgets.chat_view import ChatView
from test_agent_loop import AllowAll, DenyAll, ScriptedChatModel, tool_call


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "keep.txt").write_text("original\n", encoding="utf-8")
        (self.root / "gone.txt").write_text("bye\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_detects_new_modified_deleted(self) -> None:
        before = snapshot(self.root)
        (self.root / "added.txt").write_text("hello\n", encoding="utf-8")
        (self.root / "keep.txt").write_text("original plus more\n", encoding="utf-8")
        (self.root / "gone.txt").unlink()

        changes = changed_files(before, snapshot(self.root))

        self.assertEqual(
            [(change.path, change.kind) for change in changes],
            [
                ("added.txt", NEW),
                ("keep.txt", MODIFIED),
                ("gone.txt", DELETED),
            ],
        )

    def test_untouched_files_are_not_reported(self) -> None:
        before = snapshot(self.root)
        self.assertEqual(changed_files(before, snapshot(self.root)), [])

    def test_skip_dirs_are_ignored(self) -> None:
        noisy = self.root / "node_modules" / "pkg"
        noisy.mkdir(parents=True)
        before = snapshot(self.root)
        (noisy / "index.js").write_text("x\n", encoding="utf-8")
        after = snapshot(self.root)
        self.assertNotIn("node_modules/pkg/index.js", after)
        self.assertEqual(changed_files(before, after), [])


class EditDetailTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_modified_file_carries_line_delta(self) -> None:
        before = self.workspace.snapshot()
        self.workspace.call(
            "edit_file",
            {"path": "src/app.py", "old_string": "b\n", "new_string": "b\nBB\n"},
        )

        changes = self.workspace.file_changes(before)

        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual((change.path, change.kind), ("src/app.py", MODIFIED))
        # old_string 1 行 → new_string 2 行
        self.assertEqual((change.added, change.removed), (2, 1))

    def test_created_file_counts_as_new(self) -> None:
        before = self.workspace.snapshot()
        self.workspace.call(
            "edit_file",
            {"path": "docs/new.md", "old_string": "", "new_string": "# 标题\n\n正文\n"},
        )

        changes = self.workspace.file_changes(before)

        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0].path, changes[0].kind), ("docs/new.md", NEW))
        self.assertEqual(changes[0].added, 3)
        self.assertEqual(changes[0].removed, 0)

    def test_multiple_edits_accumulate(self) -> None:
        before = self.workspace.snapshot()
        self.workspace.call(
            "edit_file",
            {"path": "src/app.py", "old_string": "a\n", "new_string": "a\na2\n"},
        )
        self.workspace.call(
            "edit_file",
            {"path": "src/app.py", "old_string": "c\n", "new_string": "c\nc2\n"},
        )

        changes = self.workspace.file_changes(before)

        self.assertEqual((changes[0].added, changes[0].removed), (4, 2))


class RuntimeChangesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.workspace = Workspace(self.root)
        self.events: list = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_agent(self, responses, approval=None) -> AgentRuntime:
        runtime = AgentRuntime(
            ScriptedChatModel(responses=responses),
            self.workspace,
            approval or AllowAll(),
        )
        runtime.run("测试任务", [], self.events.append)
        return runtime

    def test_edit_file_shows_up_in_changes(self) -> None:
        runtime = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {
                        "path": "src/app.py",
                        "old_string": "return 1",
                        "new_string": "return 2",
                    },
                    "c1",
                ),
                AIMessage(content="改好了。"),
            ]
        )

        self.assertEqual(
            [(change.path, change.kind) for change in runtime.changes],
            [("src/app.py", MODIFIED)],
        )

    def test_command_written_file_is_detected(self) -> None:
        runtime = self.run_agent(
            [
                tool_call(
                    "run_command",
                    {"command": "echo generated > generated.txt"},
                    "c1",
                ),
                AIMessage(content="生成好了。"),
            ]
        )

        self.assertEqual(
            [(change.path, change.kind) for change in runtime.changes],
            [("generated.txt", NEW)],
        )

    def test_read_only_round_has_no_changes(self) -> None:
        runtime = self.run_agent(
            [
                tool_call("read_file", {"path": "src/app.py"}, "c1"),
                AIMessage(content="看完了。"),
            ]
        )
        self.assertEqual(runtime.changes, [])

    def test_denied_edit_has_no_changes(self) -> None:
        runtime = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {
                        "path": "src/app.py",
                        "old_string": "return 1",
                        "new_string": "return 3",
                    },
                    "c1",
                ),
                AIMessage(content="那我不改了。"),
            ],
            DenyAll(),
        )
        self.assertEqual(runtime.changes, [])

    def test_changes_are_reset_between_runs(self) -> None:
        runtime = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {
                        "path": "src/app.py",
                        "old_string": "return 1",
                        "new_string": "return 2",
                    },
                    "c1",
                ),
                AIMessage(content="改好了。"),
            ]
        )
        self.assertTrue(runtime.changes)

        # 同一个 runtime 不该跨任务复用，但真被试着重用时也不能带着上一轮的账
        runtime.run("只读任务", [], self.events.append)
        self.assertEqual(runtime.changes, [])


class ChatViewChangesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_renders_rows_and_replays(self) -> None:
        view = ChatView()
        changes = [
            FileChange("src/app.py", MODIFIED, added=3, removed=1),
            FileChange("docs/new.md", NEW, added=12),
            FileChange("old/legacy.py", DELETED),
        ]
        view.append_changes(changes)

        text = view.toPlainText()
        self.assertIn("本轮文件修改", text)
        self.assertIn("3 个文件", text)
        self.assertIn("src/app.py", text)
        self.assertIn("+3", text)
        self.assertIn("docs/new.md", text)
        self.assertIn("old/legacy.py", text)

        entries = view.entries()
        self.assertEqual(entries[-1][0], "changes")

        # 切会话后按记录重放，清单还在
        replayed = ChatView()
        replayed.replay(entries)
        self.assertIn("本轮文件修改", replayed.toPlainText())
        self.assertIn("src/app.py", replayed.toPlainText())

    def test_empty_changes_add_nothing(self) -> None:
        view = ChatView()
        view.append_changes([])
        self.assertEqual(view.toPlainText(), "")
        self.assertEqual(view.entries(), [])

    def test_long_list_is_truncated_but_saved_whole(self) -> None:
        view = ChatView()
        changes = [FileChange(f"file_{index}.py", MODIFIED, 1, 1) for index in range(25)]
        view.append_changes(changes)

        self.assertIn("还有 5 个文件", view.toPlainText())
        # 记录里保留全部，导出时才不会丢文件
        self.assertEqual(len(view.entries()[-1][1]), 25)


class ExportChangesTest(unittest.TestCase):
    def test_changes_entry_renders_as_list(self) -> None:
        session = Session.create(now=1700000000.0, workspace="")
        session.title = "改动清单"
        session.entries = [
            ("msg", "assistant", "改完了。"),
            (
                "changes",
                [
                    {"path": "src/app.py", "kind": MODIFIED, "added": 2, "removed": 1},
                    {"path": "docs/new.md", "kind": NEW, "added": 8, "removed": 0},
                    {"path": "old.py", "kind": DELETED, "added": 0, "removed": 0},
                ],
            ),
        ]

        text = session_to_markdown(session)

        self.assertIn("**本轮文件修改（3 个文件）**", text)
        self.assertIn("- `src/app.py` · 修改，+2 / −1 行", text)
        self.assertIn("- `docs/new.md` · 新建，新增 8 行", text)
        self.assertIn("- `old.py` · 删除", text)

    def test_broken_payload_is_skipped(self) -> None:
        session = Session.create(now=1700000000.0, workspace="")
        session.title = "坏数据"
        session.entries = [("changes", ["不是一条改动", {"path": ""}])]
        self.assertNotIn("本轮文件修改", session_to_markdown(session))

    def test_entry_survives_session_round_trip(self) -> None:
        session = Session.create(now=1700000000.0, workspace="")
        session.entries = [
            ("changes", [{"path": "a.py", "kind": NEW, "added": 1, "removed": 0}])
        ]
        restored = Session.from_dict(session.to_dict())
        assert restored is not None
        payload = restored.entries[0][1]
        self.assertEqual(payload[0]["path"], "a.py")
        self.assertEqual(payload[0]["kind"], NEW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
