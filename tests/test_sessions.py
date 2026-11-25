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
    SAVED_FALLBACK,
    SAVED_PRIMARY,
    STORE_FILE_NAME,
    Session,
    SessionStore,
    guess_workspace,
    make_title,
    migrate_legacy_sessions,
    same_workspace,
    workspace_store_path,
)
from app.ui.widgets import ChatView, SessionList


class SessionStoreTest(unittest.TestCase):
    """store 绑在一个工作区上，文件就在那个目录的隐藏目录里。"""

    def make_store(self, root: Path, **kwargs) -> SessionStore:
        # 旧文件与回退位置都指到临时目录里，测试之间互不影响
        kwargs.setdefault("legacy_path", root / "legacy.json")
        kwargs.setdefault("fallback_path", root / "fallback.json")
        return SessionStore(root, **kwargs)

    def test_roundtrip_keeps_entries_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
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
            store = self.make_store(Path(tmp))
            self.assertFalse(store.exists())
            self.assertEqual(store.load(), ([], ""))

    def test_store_lives_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            store = self.make_store(root)
            self.assertEqual(store.path, workspace_store_path(root))
            self.assertEqual(store.path, root / ".free-code-monkey" / STORE_FILE_NAME)

            session = Session.create(now=1.0)
            self.assertEqual(store.save([session], session.id), SAVED_PRIMARY)
            self.assertTrue(store.exists())
            self.assertTrue(store.path.is_file())

    def test_corrupt_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.make_store(root)
            store.path.parent.mkdir(parents=True)
            store.path.write_text("{ this is not json", encoding="utf-8")
            self.assertEqual(store.load(), ([], ""))
            # 坏文件不应该让之后的保存失败
            session = Session.create(now=1.0)
            store.save([session], session.id)
            sessions, active = store.load()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(active, session.id)

    def test_bad_session_entries_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            good = Session.create(now=2.0)
            payload = {
                "version": 1,
                "active": good.id,
                "sessions": [good.to_dict(), {"no_id": True}, "junk", None],
            }
            store.path.parent.mkdir(parents=True)
            store.path.write_text(json.dumps(payload), encoding="utf-8")
            sessions, active = store.load()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, good.id)
            self.assertEqual(active, good.id)

    def test_sessions_sorted_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            older = Session.create(now=100.0)
            newer = Session.create(now=200.0)
            store.save([older, newer], newer.id)
            sessions, _ = store.load()
            self.assertEqual([s.id for s in sessions], [newer.id, older.id])

    def test_save_stamps_sessions_with_workspace(self) -> None:
        """会话在文件之间搬动之后，归属字段要跟着文件走。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            store = self.make_store(root)
            session = Session.create(now=1.0, workspace="C:/somewhere/else")
            store.save([session], session.id)

            sessions, _ = store.load()
            self.assertTrue(same_workspace(sessions[0].workspace, root))

    def test_unwritable_workspace_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fallback = root / "fallback.json"
            store = SessionStore(root, fallback_path=fallback, legacy_path=root / "l.json")
            session = Session.create(now=1.0)

            # 把「工作区里的存储目录」占成一个同名文件，写入必然失败
            store.path.parent.write_text("不是目录", encoding="utf-8")

            self.assertEqual(store.save([session], session.id), SAVED_FALLBACK)
            self.assertTrue(fallback.is_file())
            sessions, active = store.load()
            self.assertEqual([s.id for s in sessions], [session.id])
            self.assertEqual(active, session.id)

    def test_both_locations_are_merged_by_id(self) -> None:
        """曾不可写、后来可写：两处的会话都要在，同一轮不能出现两次。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fallback = root / "fallback.json"
            store = SessionStore(root, fallback_path=fallback, legacy_path=root / "l.json")

            shared = Session.create(now=300.0)
            only_fallback = Session.create(now=200.0)
            only_primary = Session.create(now=100.0)
            store.path.parent.mkdir(parents=True)
            store.path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "active": only_primary.id,
                        "sessions": [shared.to_dict(), only_primary.to_dict()],
                    }
                ),
                encoding="utf-8",
            )
            fallback.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "active": only_fallback.id,
                        "sessions": [shared.to_dict(), only_fallback.to_dict()],
                    }
                ),
                encoding="utf-8",
            )

            sessions, active = store.load()
            ids = [s.id for s in sessions]
            self.assertEqual(len(ids), len(set(ids)))  # 去重
            self.assertEqual(
                set(ids), {shared.id, only_primary.id, only_fallback.id}
            )
            self.assertEqual(active, only_primary.id)


class MigrateLegacyTest(unittest.TestCase):
    """旧版全局单文件 → 按工作区存放。"""

    def write_legacy(self, path: Path, sessions: list[Session], active: str = "") -> None:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active": active,
                    "sessions": [s.to_dict() for s in sessions],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_only_sessions_of_this_workspace_are_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            other = root / "other-project"
            other.mkdir()
            mine = Session.create(now=2.0, workspace=str(root))
            theirs = Session.create(now=1.0, workspace=str(other))
            legacy = root / "legacy.json"
            self.write_legacy(legacy, [mine, theirs])

            claimed = migrate_legacy_sessions(root, [str(root)], legacy)

            self.assertEqual([s.id for s in claimed], [mine.id])

    def test_sessions_without_workspace_are_guessed_by_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            here = root / "here"
            (here / "src").mkdir(parents=True)
            (here / "src" / "app.py").write_text("x\n", encoding="utf-8")
            legacy = root / "legacy.json"

            matched = Session.create(now=2.0, workspace="")
            matched.entries = [
                ("msg", "user", "看看"),
                ("step", "▸ read_file  src/app.py", "", "ok"),
            ]
            unknown = Session.create(now=1.0, workspace="")
            unknown.entries = [("msg", "user", "你好")]
            self.write_legacy(legacy, [matched, unknown])

            # 内容里有 here 下真实存在的文件，无线索的那条归当前工作区
            claimed = migrate_legacy_sessions(here, [str(here)], legacy)
            self.assertEqual({s.id for s in claimed}, {matched.id, unknown.id})

    def test_missing_legacy_file_claims_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertEqual(
                migrate_legacy_sessions(root, [str(root)], root / "nope.json"), []
            )

    def test_workspace_file_is_created_on_first_open(self) -> None:
        """认领之后要落到本工作区的文件里，旧文件保持原样。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            legacy = root / "legacy.json"
            mine = Session.create(now=1.0, workspace=str(root))
            self.write_legacy(legacy, [mine])
            before = legacy.read_text(encoding="utf-8")

            store = SessionStore(root, legacy_path=legacy, fallback_path=root / "fb.json")
            claimed = migrate_legacy_sessions(root, [str(root)], store.legacy_path)
            store.save(claimed, mine.id)

            self.assertTrue(store.exists())
            sessions, active = store.load()
            self.assertEqual([s.id for s in sessions], [mine.id])
            self.assertEqual(active, mine.id)
            # 旧文件只读，一个字节都不该变
            self.assertEqual(legacy.read_text(encoding="utf-8"), before)

    def test_broken_history_does_not_raise(self) -> None:
        session = Session.create()
        session.messages = [{"type": "human", "data": "不是合法结构"}]
        self.assertEqual(session.history(), [])

    def test_workspace_store_path_follows_root(self) -> None:
        self.assertTrue(workspace_store_path("C:/some/project").is_absolute())
        self.assertTrue(workspace_store_path("C:/some/project").is_relative_to(
            Path("C:/some/project")
        ))

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
        # 旧版全局文件放在临时目录里：迁移相关用例往这儿写，绝不动真实 AppData
        self.legacy_path = self.root / "legacy.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def store_for(self, root: Path) -> SessionStore:
        return SessionStore(
            root,
            legacy_path=self.legacy_path,
            fallback_path=self.root / "fallback" / f"{root.name}.json",
            # 台账也要隔离：否则会写进真实的 AppData
            ledger_path=self.root / "legacy_claims.json",
        )

    def store_path(self, root: Path) -> Path:
        return workspace_store_path(root)

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
            settings, ProviderManager(settings), self.store_for
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
        """上限按工作区各算各的：一个工作区存满，不影响另一个。"""
        from app.constants import MAX_SESSIONS

        other = self.root / "other-project"
        other.mkdir()
        # 另一个工作区先自行存满，它有自己的文件
        other_store = self.store_for(other.resolve())
        other_store.save(
            [Session.create(now=float(i)) for i in range(MAX_SESSIONS)],
            "",
        )

        window = self.make_window()
        for _ in range(MAX_SESSIONS):
            window._on_new_session()

        # 当前工作区自己封顶；另一个工作区原样不动
        self.assertEqual(len(window._sessions), MAX_SESSIONS)
        self.assertEqual(len(other_store.load()[0]), MAX_SESSIONS)
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
        # 两轮各自存在自己的工作区文件里
        a_sessions, a_active = self.store_for(self.root.resolve()).load()
        b_sessions, b_active = self.store_for(other.resolve()).load()
        self.assertEqual([s.id for s in a_sessions], [a_session_id])
        self.assertEqual([s.id for s in b_sessions], [b_session_id])
        # active 也各记各的，不再被互相覆盖
        self.assertEqual(a_active, a_session_id)
        self.assertEqual(b_active, b_session_id)
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
        """写一份老格式（version 1、会话里没有 workspace）的全局会话文件。"""
        self.legacy_path.write_text(
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

    def test_migration_writes_into_workspace_not_legacy(self) -> None:
        """认领后写进工作区自己的文件，旧全局文件保持原样。"""
        mine = Session.create(now=1.0, workspace=str(self.root.resolve()))
        self._write_legacy_store([mine.to_dict()], mine.id)
        before = self.legacy_path.read_text(encoding="utf-8")

        window = self.make_window()
        window.close()

        stored = self.store_path(self.root.resolve())
        self.assertTrue(stored.is_file())
        payload = json.loads(stored.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], SessionStore.VERSION)
        self.assertEqual([s["id"] for s in payload["sessions"]], [mine.id])
        # 迁移只读旧文件
        self.assertEqual(self.legacy_path.read_text(encoding="utf-8"), before)

    def test_session_missing_workspace_is_not_hidden(self) -> None:
        """文件里的会话缺归属时，按「文件在哪」补齐，不能看不见。"""
        orphan = Session.create(now=1.0)
        orphan.entries = [("msg", "user", "别让我消失")]
        store = self.store_for(self.root.resolve())
        store.save([orphan], orphan.id)
        # 模拟把字段写丢
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        payload["sessions"][0].pop("workspace", None)
        store.path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        window = self.make_window()

        self.assertEqual([s.id for s in window._workspace_sessions()], [orphan.id])
        self.assertIn("别让我消失", window._chat_view.toPlainText())
        window.close()

    def test_legacy_sessions_are_split_by_content(self) -> None:
        """旧数据里混着几个项目的会话：各回各家，不能全归当前工作区。"""
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

        # 当前工作区只认领自己的那一轮
        self.assertEqual([s.id for s in window._workspace_sessions()], [mine.id])
        self.assertEqual([s.id for s in window._sessions], [mine.id])
        window.close()

        # 打开另一个项目时，它才认领属于自己的那轮
        theirs_window = self.make_window(other.resolve(), history=[here])
        self.assertEqual([s.id for s in theirs_window._sessions], [theirs.id])
        theirs_window.close()

    def test_migration_runs_only_once(self) -> None:
        """再次启动时不再重跑迁移：工作区文件已存在就直接读它。"""
        mine = Session.create(now=1.0, workspace=str(self.root.resolve()))
        self._write_legacy_store([mine.to_dict()], mine.id)
        self.make_window().close()

        # 迁移之后把旧文件换成完全不同的内容，重启不该再受影响
        self._write_legacy_store([], "")
        reopened = self.make_window()
        self.assertEqual([s.id for s in reopened._sessions], [mine.id])
        reopened.close()

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

        # 当前工作区自动换成了另一轮空白会话，列表里也不再出现它
        self.assertNotEqual(window._active_session_id, moved_id)
        self.assertEqual(window._session_list.count(), 1)
        self.assertNotIn(moved_id, {s.id for s in window._workspace_sessions()})
        self.assertEqual(window._chat_view.toPlainText(), "")

        # 它已经写进目标工作区自己的文件，归属也跟着变成那个目录
        moved_sessions, _active = self.store_for(other.resolve()).load()
        self.assertEqual([s.id for s in moved_sessions], [moved_id])
        self.assertTrue(same_workspace(moved_sessions[0].workspace, other))
        self.assertEqual(
            [s.id for s in self.store_for(self.root.resolve()).load()[0]],
            [window._active_session_id],
        )
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
