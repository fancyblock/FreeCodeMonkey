"""会话导出为 Markdown 的测试：不需要模型与文件对话框。

运行：.venv\\Scripts\\python.exe tests\\test_export.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow
from app.ui.export import (
    default_bulk_export_name,
    default_export_name,
    safe_filename,
    session_to_markdown,
    sessions_to_markdown,
    write_markdown,
)
from app.ui.sessions import DEFAULT_TITLE, Session, SessionStore


def make_session(title: str, entries: list, created: float = 1700000000.0) -> Session:
    session = Session.create(now=created)
    session.title = title
    session.updated_at = created
    session.entries = entries
    return session


class MarkdownFormatTest(unittest.TestCase):
    def test_single_session_contains_core_parts(self) -> None:
        session = make_session(
            "重构入口",
            [
                ("msg", "user", "帮我看下这个 bug"),
                ("step", "▸ read_file  src/app.py", "1  import os", "info"),
                ("sys", "工作区已切换"),
                ("div",),
                ("msg", "assistant", "原因在初始化顺序。"),
            ],
        )
        text = session_to_markdown(session)

        self.assertTrue(text.startswith("# 重构入口"))
        self.assertIn("**你**", text)
        self.assertIn("帮我看下这个 bug", text)
        self.assertIn("**助手**", text)
        self.assertIn("原因在初始化顺序。", text)
        # 步骤标题成为列表项，工具输出进代码块
        self.assertIn("- ▸ read_file  src/app.py", text)
        self.assertIn("```text", text)
        self.assertIn("1  import os", text)
        # 系统提示退成引用块
        self.assertIn("> _工作区已切换_", text)
        # 分隔线保留
        self.assertIn("\n---\n", text)

    def test_heading_level_is_respected(self) -> None:
        session = make_session("标题", [("msg", "user", "x")])
        self.assertIn("## 标题", session_to_markdown(session, heading_level=2))
        # 越界层级会被夹到 1..6
        self.assertIn("# 标题", session_to_markdown(session, heading_level=0))
        self.assertIn("###### 标题", session_to_markdown(session, heading_level=99))

    def test_empty_session_is_marked(self) -> None:
        text = session_to_markdown(make_session(DEFAULT_TITLE, []))
        self.assertIn("还没有内容", text)

    def test_malformed_entries_are_skipped(self) -> None:
        session = make_session(
            "乱数据",
            [None, [], ["msg"], ["unknown", "x"], ("msg", "user", "正常的一条")],
        )
        text = session_to_markdown(session)
        self.assertIn("正常的一条", text)
        # 只有一条消息，说明其余坏条目都被跳过了
        self.assertEqual(text.count("**你**"), 1)

    def test_fence_escalates_when_body_has_backticks(self) -> None:
        session = make_session(
            "围栏", [("step", "▸ run", "前\n```\n后\n```")]
        )
        text = session_to_markdown(session)
        # 正文自带三反引号，围栏必须加长，否则代码块会被提前闭合
        self.assertIn("````text", text)
        self.assertIn("````\n", text)

    def test_multiline_user_message_is_kept(self) -> None:
        session = make_session("多行", [("msg", "user", "第一行\n第二行")])
        text = session_to_markdown(session)
        self.assertIn("第一行\n第二行", text)

    def test_bulk_export_orders_chronologically(self) -> None:
        older = make_session("较早", [("msg", "user", "旧")], created=100.0)
        newer = make_session("较晚", [("msg", "user", "新")], created=200.0)
        text = sessions_to_markdown([newer, older], now=300.0)

        self.assertTrue(text.startswith("# FreeCodeMonkey 会话导出"))
        self.assertIn("共 2 个会话", text)
        self.assertEqual(text.count("**你**"), 2)
        # 早的排前面，读起来是一条时间线
        self.assertLess(text.index("## 较早"), text.index("## 较晚"))

    def test_bulk_export_separates_sessions(self) -> None:
        first = make_session("甲", [("msg", "user", "a")], created=100.0)
        second = make_session("乙", [("msg", "user", "b")], created=200.0)
        text = sessions_to_markdown([first, second], now=300.0)
        # 两个会话之间有一条分隔线
        self.assertGreaterEqual(text.count("\n---\n"), 1)


class FilenameTest(unittest.TestCase):
    def test_illegal_characters_removed(self) -> None:
        self.assertEqual(safe_filename('a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_whitespace_collapsed(self) -> None:
        self.assertEqual(safe_filename("  多   空格  "), "多 空格")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(safe_filename(""), "会话")
        self.assertEqual(safe_filename("///"), "会话")

    def test_reserved_names_prefixed(self) -> None:
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("com1"), "_com1")

    def test_trailing_dots_removed(self) -> None:
        self.assertEqual(safe_filename("title..."), "title")

    def test_long_title_truncated(self) -> None:
        result = safe_filename("很" * 200)
        self.assertLessEqual(len(result), 60)

    def test_default_names(self) -> None:
        session = make_session("重构入口", [])
        self.assertEqual(default_export_name(session), "重构入口.md")
        bulk = default_bulk_export_name(now=1700000000.0)
        self.assertRegex(bulk, r"^会话导出-\d{8}-\d{4}\.md$")


class WriteMarkdownTest(unittest.TestCase):
    def test_writes_utf8_and_appends_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "导出结果"
            written = write_markdown(target, "# 中文标题\n")
            self.assertEqual(written.name, "导出结果.md")
            self.assertEqual(written.read_text(encoding="utf-8"), "# 中文标题\n")

    def test_existing_md_suffix_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = write_markdown(Path(tmp) / "a.md", "x")
            self.assertEqual(written.name, "a.md")

    def test_creates_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = write_markdown(Path(tmp) / "sub" / "a.md", "x")
            self.assertTrue(written.is_file())


class ExportButtonsTest(unittest.TestCase):
    """按钮接线与导出流程，文件对话框用 patch 替掉。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        settings = Settings(
            QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        )
        settings.workspace_root = str(self.root)
        config = ProviderConfig(
            id="p1",
            type="openai_compatible",
            values={"name": "fake", "api_key": "sk-test", "model": "fake-model"},
        )
        settings.providers_json = ProviderStore.dumps([config])
        settings.active_provider_id = "p1"
        self.window = MainWindow(
            settings, ProviderManager(settings), SessionStore(self.root / "s.json")
        )
        self.info_messages: list[str] = []

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def _fill_current(self, text: str = "帮我看看这个 bug") -> None:
        self.window._chat_view.append_message("user", text)
        self.window._prompt_list.add_prompt(text, "msg-0")
        self.window._chat_view.append_message("assistant", "好的，我看一下。")
        self.window._save_active_session()

    def test_buttons_exist(self) -> None:
        widget = self.window._session_list
        self.assertEqual(widget._export_current_button.text(), "导出当前会话")
        self.assertEqual(widget._export_all_button.text(), "导出全部会话")

    def test_export_current_writes_file(self) -> None:
        self._fill_current("导出这一轮")
        target = self.root / "out" / "当前.md"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Markdown 文件 (*.md)"),
        ):
            self.window._session_list._export_current_button.click()

        written = self.root / "out" / "当前.md"
        self.assertTrue(written.is_file())
        text = written.read_text(encoding="utf-8")
        self.assertIn("导出这一轮", text)
        self.assertIn("好的，我看一下。", text)

    def test_export_current_includes_tool_steps(self) -> None:
        self._fill_current("带步骤的一轮")
        self.window._chat_view.append_step("▸ read_file  src/app.py", "1  import os", "info")
        self.window._save_active_session()

        target = self.root / "steps.md"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Markdown 文件 (*.md)"),
        ):
            self.window._on_export_current()

        text = target.read_text(encoding="utf-8")
        self.assertIn("▸ read_file  src/app.py", text)
        self.assertIn("1  import os", text)

    def test_export_does_not_bump_updated_at(self) -> None:
        """导出是只读操作，不该让会话看起来「刚被编辑过」。"""
        self._fill_current("导出不改时间")
        session = self.window._active_session()
        # 造一对明显早于「现在」的时间戳，导出后必须原样保留
        session.created_at = 1600000000.0
        session.updated_at = 1600000600.0
        before = session.updated_at

        target = self.root / "时间.md"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ):
            self.window._on_export_current()

        self.assertEqual(self.window._active_session().updated_at, before)
        # 文件里记的是那个旧时间，而不是导出当下的时间
        self.assertIn("最后更新 2020-09-13", target.read_text(encoding="utf-8"))

    def test_export_current_skips_when_empty(self) -> None:
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName"
        ) as dialog, patch(
            "app.ui.main_window.QMessageBox.information",
            side_effect=lambda *a, **k: self.info_messages.append(a[2]),
        ):
            self.window._on_export_current()
        # 空会话不该弹保存框
        dialog.assert_not_called()
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("还没有内容", self.info_messages[0])

    def test_export_all_writes_every_session(self) -> None:
        self._fill_current("第一轮的问题")
        self.window._on_new_session()
        self._fill_current("第二轮的问题")

        target = self.root / "全部.md"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Markdown 文件 (*.md)"),
        ):
            self.window._session_list._export_all_button.click()

        text = target.read_text(encoding="utf-8")
        self.assertIn("第一轮的问题", text)
        self.assertIn("第二轮的问题", text)
        self.assertIn("共 2 个会话", text)

    def test_export_all_skips_empty_sessions(self) -> None:
        self._fill_current("唯一有内容的")
        self.window._on_new_session()  # 多出一个空白会话

        target = self.root / "非空.md"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Markdown 文件 (*.md)"),
        ):
            self.window._on_export_all()

        text = target.read_text(encoding="utf-8")
        self.assertIn("共 1 个会话", text)
        self.assertNotIn("还没有内容", text)

    def test_export_all_with_nothing_says_so(self) -> None:
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName"
        ) as dialog, patch(
            "app.ui.main_window.QMessageBox.information",
            side_effect=lambda *a, **k: self.info_messages.append(a[2]),
        ):
            self.window._on_export_all()
        dialog.assert_not_called()
        self.assertIn("还没有任何有内容的会话", self.info_messages[0])

    def test_cancel_writes_nothing(self) -> None:
        self._fill_current("会被取消")
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            self.window._on_export_current()
        self.assertEqual(list(self.root.glob("*.md")), [])

    def test_write_error_is_reported(self) -> None:
        self._fill_current("会写失败")
        warnings: list[str] = []
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(self.root / "x.md"), ""),
        ), patch(
            "app.ui.main_window.write_markdown", side_effect=OSError("磁盘已满")
        ), patch(
            "app.ui.main_window.QMessageBox.warning",
            side_effect=lambda *a, **k: warnings.append(a[2]),
        ):
            self.window._on_export_current()
        self.assertEqual(len(warnings), 1)
        self.assertIn("磁盘已满", warnings[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
