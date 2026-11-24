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
    make_title,
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

    def make_window(self) -> MainWindow:
        settings = Settings(
            QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        )
        settings.workspace_root = str(self.root)
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
        # 预置满额会话，再新建一个应该把最旧的挤掉
        window._sessions = [Session.create(now=float(i)) for i in range(MAX_SESSIONS)]
        window._active_session_id = window._sessions[0].id
        oldest_id = window._sessions[-1].id
        window._on_new_session()
        self.assertEqual(len(window._sessions), MAX_SESSIONS)
        self.assertNotIn(oldest_id, {s.id for s in window._sessions})
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
