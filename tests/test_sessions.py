"""会话列表与持久化测试：不需要模型，offscreen 运行。

运行：.venv\\Scripts\\python.exe tests\\test_sessions.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage
from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtWidgets import QApplication

from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow
from app.ui.sessions import (
    DEFAULT_TITLE,
    Session,
    SessionStore,
    default_store_path,
    guess_workspace,
    make_title,
    same_workspace,
)
from app.ui.widgets import ChatView, SessionList


class SessionStoreTest(unittest.TestCase):
    def test_roundtrip_keeps_entries_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            session = Session.create(now=1000.0)
            session.title = "改个 bug"
            session.entries = [
                ("msg", "user", "改个 bug"),
                ("step", "→ read_file", "内容", "ok"),
                ("msg", "assistant", "改好了"),
            ]
            session.set_history([HumanMessage("改个 bug"), AIMessage("改好了")])

            store.save([session], session.id)
            sessions, active = store.load()

            self.assertEqual(active, session.id)
            self.assertEqual(len(sessions), 1)
            loaded = sessions[0]
            self.assertEqual(loaded.id, session.id)
            self.assertEqual(loaded.title, "改个 bug")
            self.assertEqual(len(loaded.entries), 3)
            self.assertEqual(loaded.entries[0], ["msg", "user", "改个 bug"])
            history = loaded.history()
            self.assertEqual([type(m).__name__ for m in history],
                             ["HumanMessage", "AIMessage"])
            self.assertEqual(history[0].content, "改个 bug")

    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "nope.json")
            self.assertEqual(store.load(), ([], ""))

    def test_corrupt_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            path.write_text("{ this is not json", encoding="utf-8")
            store = SessionStore(path)
            self.assertEqual(store.load(), ([], ""))
            # 坏文件不应该让之后的保存失败
            session = Session.create(now=1.0)
            store.save([session], session.id)
            sessions, active = store.load()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(active, session.id)

    def test_bad_session_entries_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            good = Session.create(now=2.0)
            payload = {
                "version": 1,
                "active": good.id,
                "sessions": [good.to_dict(), {"no_id": True}, "junk", None],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            sessions, active = SessionStore(path).load()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, good.id)
            self.assertEqual(active, good.id)

    def test_sessions_sorted_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            older = Session.create(now=100.0)
            newer = Session.create(now=200.0)
            store.save([older, newer], newer.id)
            sessions, _ = store.load()
            self.assertEqual([s.id for s in sessions], [newer.id, older.id])

    def test_broken_history_does_not_raise(self) -> None:
        session = Session.create()
        session.messages = [{"type": "human", "data": "不是合法结构"}]
        self.assertEqual(session.history(), [])

    def test_default_store_path_is_absolute(self) -> None:
        self.assertTrue(default_store_path().is_absolute())

    def test_make_title_truncates_and_collapses(self) -> None:
        self.assertEqual(make_title("  多   个 空格\n换行  "), "多 个 空格 换行")
        self.assertEqual(make_title(""), DEFAULT_TITLE)
        long_text = "很" * 40
        self.assertEqual(len(make_title(long_text)), 25)  # 24 字 + 省略号
        self.assertTrue(make_title(long_text).endswith("…"))


class GuessWorkspaceTest(unittest.TestCase):
    """按内容反推会话属于哪个工作区。"""

    def test_paths_that_exist_under_a_root_win(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "project-a"
            b = root / "project-b"
            (b / "src").mkdir(parents=True)
            (b / "src" / "main.cs").write_text("x\n", encoding="utf-8")
            entries = [("step", "▸ read_file  src/main.cs", "", "ok")]

            self.assertEqual(guess_workspace(entries, [str(a), str(b)]), str(b))

    def test_directory_listing_is_evidence(self) -> None:
        """列目录的输出里没有文件名，靠目录本身判断。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unity = root / "unity-project"
            (unity / "Assets" / "Game").mkdir(parents=True)
            (unity / "ProjectSettings").mkdir()
            plain = root / "plain-project"
            plain.mkdir()
            entries = [
                ("step", "→ list_dir", "# .（depth=1）\nAssets/\nProjectSettings/", "ok")
            ]

            self.assertEqual(guess_workspace(entries, [str(plain), str(unity)]), str(unity))

    def test_full_path_and_directory_name_are_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "alpha"
            b = root / "beta"
            a.mkdir()
            b.mkdir()
            entries = [("sys", f"工作区已切换到 {b}")]
            self.assertEqual(guess_workspace(entries, [str(a), str(b)]), str(b))
            # 只提到目录名也算线索
            self.assertEqual(
                guess_workspace([("msg", "user", "帮 beta 加个功能")], [str(a), str(b)]),
                str(b),
            )

    def test_no_clue_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("msg", "user", "你好")]
            self.assertEqual(guess_workspace(entries, [tmp]), "")
            self.assertEqual(guess_workspace([], [tmp]), "")
            self.assertEqual(guess_workspace(entries, []), "")

    def test_tie_prefers_the_first_candidate(self) -> None:
        # 两个目录名同样出现一次时，保留候选列表里靠前的（调用方把当前工作区放最前）
        entries = [("msg", "user", "alpha 和 beta 都看一下")]
        self.assertEqual(guess_workspace(entries, ["alpha", "beta"]), "alpha")


class SessionListWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_set_sessions_and_select(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "tip-a"), ("b", "第二个", "")])
        self.assertEqual(widget.count(), 2)
        self.assertTrue(widget.select("b"))
        self.assertEqual(widget.current_id(), "b")
        self.assertEqual(widget._list.item(0).toolTip(), "tip-a")
        self.assertFalse(widget.select("missing"))

    def test_double_click_emits_session_id(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", ""), ("b", "第二个", "")])
        seen: list[str] = []
        widget.session_activated.connect(seen.append)
        widget._list.itemDoubleClicked.emit(widget._list.item(1))
        self.assertEqual(seen, ["b"])

    def test_context_menu_on_item_emits_delete(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", ""), ("b", "第二个", "")])
        seen: list[str] = []
        widget.session_delete_requested.connect(seen.append)

        # 菜单是模态的，这里只验证「点在条目上会选中正确的 id」这一步
        item = widget._list.item(1)
        rect = widget._list.visualItemRect(item)
        hit = widget._list.itemAt(rect.center())
        self.assertIsNotNone(hit)
        self.assertEqual(hit.data(Qt.ItemDataRole.UserRole), "b")
        self.assertEqual(seen, [])

    def test_set_running_marks_only_that_item(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "tip-a"), ("b", "第二个", "")])

        widget.set_running("b")

        item_a, item_b = widget._list.item(0), widget._list.item(1)
        self.assertEqual(widget.running_id(), "b")
        # 进行中的那条：加粗 + 有圆点图标
        self.assertTrue(item_b.font().bold())
        self.assertFalse(item_b.icon().isNull())
        # 其他条目不受影响
        self.assertFalse(item_a.font().bold())
        self.assertTrue(item_a.icon().isNull())
        # 标题本身不被改写，拖宽也不会露出标记
        self.assertEqual(item_b.text(), "第二个")

    def test_set_running_none_clears(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "tip-a")])
        widget.set_running("a")
        widget.set_running(None)

        item = widget._list.item(0)
        self.assertEqual(widget.running_id(), "")
        self.assertFalse(item.font().bold())
        self.assertTrue(item.icon().isNull())
        # 原始提示保留，没有残留「正在执行」
        self.assertEqual(item.toolTip(), "tip-a")

    def test_running_hint_in_tooltip(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "tip-a")])
        widget.set_running("a")
        self.assertIn("正在执行任务", widget._list.item(0).toolTip())

    def test_rebuild_keeps_running_mark(self) -> None:
        """列表重建（例如标题更新）后标记要还在。"""
        widget = SessionList()
        widget.set_sessions([("a", "旧标题", "")])
        widget.set_running("a")

        widget.set_sessions([("a", "新标题", "")])

        item = widget._list.item(0)
        self.assertEqual(item.text(), "新标题")
        self.assertTrue(item.font().bold())
        self.assertFalse(item.icon().isNull())

    def test_set_running_unknown_id_marks_nothing(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "")])
        widget.set_running("不存在")
        self.assertTrue(widget._list.item(0).icon().isNull())


class SessionListRunningStyleTest(unittest.TestCase):
    """「进行中」样式用图标 + 粗体，而不是文字颜色。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dot_icon_renders_visible_pixels(self) -> None:
        from app.ui.icons import icon

        pixmap = icon("dot", "#2f6feb", 10).pixmap(10, 10)
        image = pixmap.toImage()
        painted = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )
        self.assertGreater(painted, 0, "圆点图标是空的，进行中状态会看不见")

    def test_dot_respects_requested_color(self) -> None:
        from app.ui.icons import icon

        red = icon("dot", "#ff0000", 16).pixmap(16, 16).toImage()
        blue = icon("dot", "#0000ff", 16).pixmap(16, 16).toImage()
        self.assertNotEqual(red, blue)


class ContextMenuEmptyAreaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_context_menu_on_empty_area_does_not_emit(self) -> None:
        widget = SessionList()
        widget.set_sessions([("a", "第一个", "")])
        seen: list[str] = []
        widget.session_delete_requested.connect(seen.append)
        # 条目下方的空白处不应该命中任何条目（右键逻辑据此直接返回）
        below = widget._list.visualItemRect(widget._list.item(0)).bottom() + 200
        self.assertIsNone(widget._list.itemAt(QPoint(4, below)))
        self.assertEqual(seen, [])


class ChatViewReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_entries_recorded_and_replayed(self) -> None:
        view = ChatView()
        mark = view.append_message("user", "第一条")
        view.append_step("▸ read_file", "内容", "ok")
        view.append_message("assistant", "回复")
        view.append_system("提示")
        view.append_divider()

        entries = view.entries()
        self.assertEqual(entries[0], ("msg", "user", "第一条"))
        self.assertEqual(entries[1], ("step", "▸ read_file", "内容", "ok"))
        self.assertEqual(entries[3], ("sys", "提示"))
        self.assertEqual(entries[4], ("div",))

        prompts = view.replay(entries)
        # 重放后标记名一致，提示词列表存的标记仍然可用
        self.assertEqual(prompts, [("第一条", mark)])
        self.assertEqual(view.entries(), entries)
        self.assertIn("第一条", view.toPlainText())
        self.assertIn("回复", view.toPlainText())

    def test_clear_history_drops_entries(self) -> None:
        view = ChatView()
        view.append_message("user", "x")
        view.clear_history()
        self.assertEqual(view.entries(), [])
        self.assertEqual(view.toPlainText(), "")

    def test_replay_skips_malformed_entries(self) -> None:
        view = ChatView()
        prompts = view.replay([None, [], ["msg"], ["unknown", "x"], ["msg", "user", "ok"]])
        self.assertEqual(prompts, [("ok", "msg-0")])

    def test_long_lines_wrap_instead_of_scrolling_horizontally(self) -> None:
        view = ChatView()
        view.resize(320, 240)
        view.show()
        self.app.processEvents()
        # 步骤输出走 <pre>，消息是普通段落；两种都不能把视图撑宽
        view.append_step("▸ run_command", "x" * 300)
        view.append_message("user", "y" * 300)
        self.app.processEvents()
        self.assertEqual(
            view.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.assertEqual(view.horizontalScrollBar().maximum(), 0)
        view.close()


class SessionWindowTest(unittest.TestCase):
    """主窗口里的新建 / 切换 / 持久化行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store_path = self.root / "sessions.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_window(
        self, workspace: Path | None = None, history: list[Path] | None = None
    ) -> MainWindow:
        settings = Settings(
            QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        )
        settings.workspace_root = str(workspace or self.root)
        if history:
            settings.workspace_history = [str(path.resolve()) for path in history]
        # 配一个假 provider：输入区才会启用，按钮的可用状态也才有意义
        config = ProviderConfig(
            id="p1",
            type="openai_compatible",
            values={"name": "fake", "api_key": "sk-test", "model": "fake-model"},
        )
        settings.providers_json = ProviderStore.dumps([config])
        settings.active_provider_id = "p1"
        return MainWindow(
            settings, ProviderManager(settings), SessionStore(self.store_path)
        )

    def test_starts_with_one_session(self) -> None:
        window = self.make_window()
        self.assertEqual(len(window._sessions), 1)
        self.assertEqual(window._session_list.count(), 1)
        self.assertEqual(window._active_session_id, window._sessions[0].id)
        window.close()

    def test_new_session_clears_and_keeps_previous(self) -> None:
        window = self.make_window()
        first_id = window._active_session_id
        window._chat_view.append_message("user", "第一个会话的问题")
        window._prompt_list.add_prompt("第一个会话的问题", "msg-0")
        window._save_active_session()

        window._on_new_session()

        self.assertEqual(len(window._sessions), 2)
        self.assertNotEqual(window._active_session_id, first_id)
        # 新会话是空的
        self.assertEqual(window._chat_view.toPlainText(), "")
        self.assertEqual(window._prompt_list.count(), 0)
        # 旧会话还在列表里，标题已按第一条消息生成
        titles = [
            window._session_list._list.item(i).text()
            for i in range(window._session_list.count())
        ]
        self.assertIn("第一个会话的问题", titles)
        window.close()

    def test_switch_restores_prompts_and_chat(self) -> None:
        window = self.make_window()
        first_id = window._active_session_id
        window._chat_view.append_message("user", "第一轮")
        window._prompt_list.add_prompt("第一轮", "msg-0")
        window._chat_view.append_message("assistant", "第一轮回复")
        window._save_active_session()

        window._on_new_session()
        window._chat_view.append_message("user", "第二轮")
        window._prompt_list.add_prompt("第二轮", "msg-0")
        window._save_active_session()

        window._on_session_activated(first_id)

        self.assertEqual(window._active_session_id, first_id)
        self.assertEqual(window._prompt_list.count(), 1)
        self.assertEqual(window._prompt_list.item_mark(0), "msg-0")
        self.assertIn("第一轮", window._chat_view.toPlainText())
        self.assertNotIn("第二轮", window._chat_view.toPlainText())
        # 提示词的标记在新铺开的视图里仍然能定位
        self.assertTrue(window._chat_view.scroll_to_mark("msg-0"))
        window.close()

    def test_switch_restores_agent_history(self) -> None:
        window = self.make_window()
        first_id = window._active_session_id
        window._history = [HumanMessage("旧问题"), AIMessage("旧回答")]
        window._save_active_session()

        window._on_new_session()
        self.assertEqual(window._history, [])  # 新会话不继承记忆

        window._on_session_activated(first_id)
        self.assertEqual(len(window._history), 2)
        self.assertEqual(window._history[0].content, "旧问题")
        window.close()

    def test_sessions_persist_across_restart(self) -> None:
        window = self.make_window()
        window._chat_view.append_message("user", "重启后还应该看得到")
        window._prompt_list.add_prompt("重启后还应该看得到", "msg-0")
        window._save_active_session()
        saved_id = window._active_session_id
        window.close()

        reopened = self.make_window()
        self.assertEqual([s.id for s in reopened._sessions], [saved_id])
        self.assertEqual(reopened._active_session_id, saved_id)
        self.assertIn("重启后还应该看得到", reopened._chat_view.toPlainText())
        self.assertEqual(reopened._prompt_list.count(), 1)
        reopened.close()

    def test_cannot_switch_while_running(self) -> None:
        window = self.make_window()
        first_id = window._active_session_id
        window._on_new_session()
        second_id = window._active_session_id
        # 假装有一个任务在跑
        window._active_handles = [object()]
        window._on_session_activated(first_id)
        self.assertEqual(window._active_session_id, second_id)

        window._on_new_session()
        self.assertEqual(len(window._sessions), 2)  # 没有新建
        window._active_handles = []
        window.close()

    def test_session_cap_drops_oldest(self) -> None:
        from app.constants import MAX_SESSIONS

        window = self.make_window()
        workspace = str(window._workspace_root)
        # 预置满额会话，再新建一个应该把最旧的挤掉
        window._sessions = [
            Session.create(now=float(i), workspace=workspace)
            for i in range(MAX_SESSIONS)
        ]
        window._active_session_id = window._sessions[0].id
        oldest_id = min(
            window._sessions, key=lambda session: session.created_at
        ).id
        window._on_new_session()
        self.assertEqual(len(window._sessions), MAX_SESSIONS)
        self.assertNotIn(oldest_id, {s.id for s in window._sessions})
        window.close()

    def test_session_cap_is_per_workspace(self) -> None:
        from app.constants import MAX_SESSIONS

        window = self.make_window()
        workspace = str(window._workspace_root)
        other = self.root / "other-project"
        other.mkdir()
        # 另一个工作区也存满会话；当前工作区新建时不该动它们的
        window._sessions = [
            Session.create(now=float(i), workspace=str(other.resolve()))
            for i in range(MAX_SESSIONS)
        ]
        window._sessions.append(
            Session.create(now=900.0, workspace=workspace)
        )
        window._active_session_id = window._sessions[-1].id

        window._on_new_session()

        self.assertEqual(
            len(window._workspace_sessions()), 2
        )  # 原来那轮 + 新建的
        self.assertEqual(
            len([s for s in window._sessions if s.workspace == str(other.resolve())]),
            MAX_SESSIONS,
        )
        window.close()

    def test_switch_workspace_swaps_sessions_and_chat(self) -> None:
        """切换工作区时，会话列表、提示词列表与对话区一起换。"""
        window = self.make_window()
        window._chat_view.append_message("user", "A 里的提问")
        window._prompt_list.add_prompt("A 里的提问", "msg-0")
        window._save_active_session()
        a_session_id = window._active_session_id

        other = self.root / "other-project"
        other.mkdir()
        window._apply_workspace(other.resolve())

        # 新工作区从空白开始：只有一轮空会话，看不到 A 的内容
        self.assertEqual(window._session_list.count(), 1)
        self.assertNotEqual(window._active_session_id, a_session_id)
        self.assertEqual(window._prompt_list.count(), 0)
        self.assertNotIn("A 里的提问", window._chat_view.toPlainText())

        window._chat_view.append_message("user", "B 里的提问")
        window._prompt_list.add_prompt("B 里的提问", "msg-0")
        window._save_active_session()
        b_session_id = window._active_session_id

        # 切回 A：列表、提示词与对话都回到 A 自己的状态
        window._apply_workspace(self.root.resolve())
        self.assertEqual(window._active_session_id, a_session_id)
        self.assertEqual(window._session_list.count(), 1)
        self.assertEqual(window._prompt_list.count(), 1)
        self.assertIn("A 里的提问", window._chat_view.toPlainText())
        self.assertNotIn("B 里的提问", window._chat_view.toPlainText())
        # 两轮的会话都还在，只是各归各的工作区
        self.assertEqual({s.id for s in window._sessions}, {a_session_id, b_session_id})
        window.close()

    def test_workspace_sessions_persist_across_restart(self) -> None:
        window = self.make_window()
        window._chat_view.append_message("user", "A 里的内容")
        window._save_active_session()
        a_session_id = window._active_session_id

        other = self.root / "other-project"
        other.mkdir()
        window._apply_workspace(other.resolve())
        window._chat_view.append_message("user", "B 里的内容")
        window._save_active_session()
        b_session_id = window._active_session_id
        window.close()

        # 以 B 为当前工作区重启：只看到 B 自己的会话
        reopened = self.make_window(other.resolve())
        self.assertEqual([s.id for s in reopened._workspace_sessions()], [b_session_id])
        self.assertIn("B 里的内容", reopened._chat_view.toPlainText())
        reopened.close()

        # 再以 A 重启：A 的记录原样回来，B 的不混进来
        back = self.make_window(self.root.resolve())
        self.assertEqual([s.id for s in back._workspace_sessions()], [a_session_id])
        self.assertIn("A 里的内容", back._chat_view.toPlainText())
        self.assertNotIn("B 里的内容", back._chat_view.toPlainText())
        back.close()

    def _write_legacy_store(self, sessions: list[dict], active: str = "") -> None:
        """写一份老格式（version 1、会话里没有 workspace）的会话文件。"""
        self.store_path.write_text(
            json.dumps(
                {"version": 1, "active": active, "sessions": sessions},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_legacy_session_without_clues_adopts_current_workspace(self) -> None:
        # 老版本存的会话既没有 workspace 字段、内容里也没有线索
        legacy = Session.create(now=1.0)
        data = legacy.to_dict()
        data.pop("workspace")
        self._write_legacy_store([data], legacy.id)

        window = self.make_window()

        self.assertEqual([s.id for s in window._workspace_sessions()], [legacy.id])
        self.assertEqual(window._active_session_id, legacy.id)
        window.close()

    def test_session_missing_workspace_is_not_hidden(self) -> None:
        """新格式文件里个别会话缺归属时，也要落到当前工作区，不能看不见。"""
        orphan = Session.create(now=1.0)
        orphan.entries = [("msg", "user", "别让我消失")]
        SessionStore(self.store_path).save([orphan], orphan.id)
        # 模拟把字段写丢
        payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        payload["sessions"][0].pop("workspace", None)
        self.store_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        window = self.make_window()

        self.assertEqual([s.id for s in window._workspace_sessions()], [orphan.id])
        self.assertIn("别让我消失", window._chat_view.toPlainText())
        window.close()

    def test_legacy_sessions_are_split_by_content(self) -> None:
        """旧数据里混着几个项目的会话：按内容各回各家，不能全归当前工作区。"""
        here = self.root.resolve()
        # 另一个项目：有一个真实存在的文件，会话里提到它
        other = self.root / "unity-project"
        (other / "Assets" / "Game").mkdir(parents=True)
        (other / "Assets" / "Game" / "Hud.cs").write_text("class Hud {}\n", encoding="utf-8")

        mine = Session.create(now=2.0)
        mine.entries = [("msg", "user", "改一下本项目的 sessions.py")]
        theirs = Session.create(now=1.0)
        theirs.entries = [
            ("msg", "user", "看看 HUD"),
            ("step", "▸ read_file  Assets/Game/Hud.cs", "class Hud", "ok"),
        ]
        self._write_legacy_store(
            [mine.to_dict() | {"workspace": ""}, theirs.to_dict() | {"workspace": ""}],
            theirs.id,
        )

        # 另一个项目在历史目录里，推断时才会被当成候选
        window = self.make_window(history=[other])

        by_id = {s.id: s for s in window._sessions}
        self.assertTrue(same_workspace(by_id[mine.id].workspace, self.root))
        self.assertTrue(same_workspace(by_id[theirs.id].workspace, other))
        # 当前工作区只看得到自己的那一轮
        self.assertEqual([s.id for s in window._workspace_sessions()], [mine.id])
        window.close()

    def test_migrated_workspace_is_persisted(self) -> None:
        """推断结果要落盘：下次启动不能再按陈旧数据重算。"""
        other = self.root / "unity-project"
        (other / "Assets").mkdir(parents=True)
        (other / "Assets" / "Hud.cs").write_text("x\n", encoding="utf-8")
        theirs = Session.create(now=1.0)
        theirs.entries = [("step", "▸ read_file  Assets/Hud.cs", "", "ok")]
        data = theirs.to_dict() | {"workspace": ""}
        self._write_legacy_store([data], theirs.id)

        window = self.make_window(history=[other])
        window.close()

        sessions, _active = SessionStore(self.store_path).load()
        saved = next((s for s in sessions if s.id == theirs.id), None)
        self.assertIsNotNone(saved)
        self.assertEqual(str(Path(saved.workspace).resolve()), str(other.resolve()))
        # 文件已升级到新版本，旧数据不会再被当成「待迁移」
        payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], SessionStore.VERSION)

    def test_switch_workspace_does_not_write_into_session(self) -> None:
        """切换工作区只更新状态栏，不能往新工作区的会话里塞记录。"""
        window = self.make_window()
        other = self.root / "other-project"
        other.mkdir()
        window._apply_workspace(other.resolve())

        self.assertEqual(window._chat_view.entries(), [])
        self.assertEqual(window._session_list.count(), 1)
        self.assertIn("工作区已切换", window.statusBar().currentMessage())
        window.close()

    def test_move_session_to_another_workspace(self) -> None:
        window = self.make_window()
        window._chat_view.append_message("user", "其实是在另一个项目做的")
        window._save_active_session()
        moved_id = window._active_session_id

        other = self.root / "other-project"
        other.mkdir()
        window._workspace_history = [str(other.resolve())]
        window._sync_session_list()
        self.assertEqual(window._session_list.move_targets(),
                         [(other.name, str(other.resolve()))])

        window._on_move_session(moved_id, str(other.resolve()))

        by_id = {s.id: s for s in window._sessions}
        self.assertTrue(same_workspace(by_id[moved_id].workspace, other))
        # 当前工作区自动换成了另一轮空白会话，列表里也不再出现它
        self.assertNotEqual(window._active_session_id, moved_id)
        self.assertEqual(window._session_list.count(), 1)
        self.assertNotIn(moved_id, {s.id for s in window._workspace_sessions()})
        self.assertEqual(window._chat_view.toPlainText(), "")
        window.close()

    def test_move_blocked_while_running(self) -> None:
        window = self.make_window()
        moved_id = window._active_session_id
        other = self.root / "other-project"
        other.mkdir()
        window._active_handles = [object()]

        window._on_move_session(moved_id, str(other.resolve()))

        self.assertTrue(same_workspace(window._active_session().workspace, self.root))
        window._active_handles = []
        window.close()

    def test_clear_history_resets_title(self) -> None:
        window = self.make_window()
        window._chat_view.append_message("user", "会被清掉的内容")
        window._save_active_session()
        self.assertNotEqual(window._active_session().title, DEFAULT_TITLE)

        window._clear_history()
        self.assertEqual(window._active_session().title, DEFAULT_TITLE)
        self.assertEqual(window._prompt_list.count(), 0)
        self.assertEqual(window._chat_view.toPlainText(), "")
        window.close()

    def _three_sessions(self, window: MainWindow) -> list[str]:
        """造出三个有内容的会话，返回按列表顺序的 id。"""
        window._chat_view.append_message("user", "第一轮的提问")
        window._prompt_list.add_prompt("第一轮的提问", "msg-0")
        window._save_active_session()
        first = window._active_session_id

        window._on_new_session()
        window._chat_view.append_message("user", "第二轮的提问")
        window._save_active_session()
        second = window._active_session_id

        window._on_new_session()
        window._chat_view.append_message("user", "第三轮的提问")
        window._save_active_session()
        third = window._active_session_id
        return [third, second, first]

    def test_delete_other_session_keeps_current(self) -> None:
        window = self.make_window()
        third, second, _first = self._three_sessions(window)
        window._confirm_delete = lambda session: True

        window._on_delete_session(second)

        self.assertEqual(len(window._sessions), 2)
        self.assertEqual(window._active_session_id, third)
        self.assertNotIn(second, {s.id for s in window._sessions})
        # 界面上仍是原来那一轮的内容
        self.assertIn("第三轮的提问", window._chat_view.toPlainText())
        self.assertEqual(window._session_list.count(), 2)
        window.close()

    def test_delete_active_session_switches_to_neighbour(self) -> None:
        window = self.make_window()
        third, second, _first = self._three_sessions(window)
        window._confirm_delete = lambda session: True

        window._on_delete_session(third)  # 当前就是 third

        self.assertEqual(len(window._sessions), 2)
        self.assertNotEqual(window._active_session_id, third)
        # 删掉最上面那个后，接着往后挑一个（也就是它下面那一项）
        self.assertEqual(window._active_session_id, second)
        self.assertIn("第二轮的提问", window._chat_view.toPlainText())
        self.assertNotIn("第三轮的提问", window._chat_view.toPlainText())
        self.assertEqual(window._prompt_list.count(), 1)
        window.close()

    def test_delete_last_session_leaves_blank_one(self) -> None:
        window = self.make_window()
        only_id = window._active_session_id
        window._chat_view.append_message("user", "唯一的一轮")
        window._save_active_session()
        window._confirm_delete = lambda session: True

        window._on_delete_session(only_id)

        self.assertEqual(len(window._sessions), 1)
        self.assertNotEqual(window._active_session_id, only_id)
        self.assertEqual(window._session_list.count(), 1)
        # 新补的会话是空白的
        self.assertEqual(window._chat_view.toPlainText(), "")
        self.assertEqual(window._prompt_list.count(), 0)
        self.assertEqual(window._active_session().title, DEFAULT_TITLE)
        window.close()

    def test_delete_cancelled_keeps_everything(self) -> None:
        window = self.make_window()
        self._three_sessions(window)
        window._confirm_delete = lambda session: False

        window._on_delete_session(window._active_session_id)

        self.assertEqual(len(window._sessions), 3)
        window.close()

    def test_delete_blocked_while_running(self) -> None:
        window = self.make_window()
        _third, second, _first = self._three_sessions(window)
        window._confirm_delete = lambda session: True
        window._active_handles = [object()]

        window._on_delete_session(second)

        self.assertEqual(len(window._sessions), 3)
        window._active_handles = []
        window.close()

    def test_delete_persists_across_restart(self) -> None:
        window = self.make_window()
        third, second, first = self._three_sessions(window)
        window._confirm_delete = lambda session: True
        window._on_delete_session(second)
        window.close()

        reopened = self.make_window()
        self.assertEqual({s.id for s in reopened._sessions}, {third, first})
        self.assertNotIn(second, {s.id for s in reopened._sessions})
        reopened.close()

    def test_running_marks_session_during_task(self) -> None:
        """提交任务后，列表里该会话显示「进行中」；任务结束清除。"""
        window = self.make_window()
        session_id = window._active_session_id
        self.assertEqual(window._session_list.running_id(), "")

        # 用桩替掉真实线程，避免真的去调模型
        class _Signal:
            def connect(self, slot) -> None:
                pass

        class _Stub:
            result = _Signal()
            failed = _Signal()

        class _Thread:
            finished = _Signal()

        class _Handle:
            worker = _Stub()
            thread = _Thread()

        window._manager.build_model = lambda cfg: object()
        with patch("app.ui.main_window.run_in_thread", return_value=_Handle()):
            window._on_submit("跑一个任务")

        self.assertEqual(window._session_list.running_id(), session_id)
        self.assertTrue(window._session_list._list.item(0).font().bold())

        window._finish_task("就绪")
        self.assertEqual(window._session_list.running_id(), "")
        self.assertFalse(window._session_list._list.item(0).font().bold())

        # 收尾：桩句柄不能留给 closeEvent 去 quit
        window._active_handles.clear()
        window.close()

    def test_new_session_button_wired(self) -> None:
        window = self.make_window()
        self.assertEqual(len(window._sessions), 1)
        window._prompt_input._new_session_button.click()
        self.assertEqual(len(window._sessions), 2)
        window.close()

    def test_new_session_button_disabled_while_running(self) -> None:
        window = self.make_window()
        self.assertTrue(window._prompt_input._new_session_button.isEnabled())
        window._prompt_input.set_running(True)
        self.assertFalse(window._prompt_input._new_session_button.isEnabled())
        window._prompt_input.set_running(False)
        self.assertTrue(window._prompt_input._new_session_button.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
