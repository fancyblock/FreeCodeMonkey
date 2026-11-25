"""界面装配冒烟测试：不弹真实窗口，也不需要模型。

运行：.venv\\Scripts\\python.exe tests\\test_ui_smoke.py
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

from app.agent import ApprovalRequest
from app.config import Settings
from app.constants import APP_NAME
from app.core import ProviderConfig, ProviderManager
from app.ui import MainWindow
from store_support import workspace_store_factory
from app.ui.widgets import ApprovalPanel


class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        # 每个窗口配一个临时会话文件，测试之间互不影响，也不碰真实用户数据
        cls._store_dirs: list[tempfile.TemporaryDirectory] = []

    @classmethod
    def tearDownClass(cls) -> None:
        for directory in cls._store_dirs:
            directory.cleanup()

    def _make_settings(self, directory: str) -> Settings:
        qsettings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
        settings = Settings(qsettings)
        settings.workspace_root = directory
        return settings

    def _make_window(self, settings: Settings) -> MainWindow:
        store_dir = tempfile.TemporaryDirectory()
        type(self)._store_dirs.append(store_dir)
        factory = workspace_store_factory(Path(store_dir.name))
        return MainWindow(settings, ProviderManager(settings), factory)

    def test_window_builds_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = self._make_window(settings)
            window.show()
            self.assertTrue(window.windowTitle().startswith(APP_NAME))
            # 没有 provider 时输入区整体禁用，避免用户白输入
            self.assertFalse(window._prompt_input.isEnabled())
            self.assertEqual(str(window._workspace_root), str(Path(tmp).resolve()))
            # 默认 Build，可切换到只读的 Plan
            self.assertEqual(window._prompt_input.mode(), "build")
            combo = window._prompt_input._mode_combo
            combo.setCurrentIndex(combo.findData("plan"))
            self.assertEqual(window._prompt_input.mode(), "plan")
            self.assertEqual(
                [combo.itemData(i) for i in range(combo.count())], ["plan", "build"]
            )
            window.close()

    def test_prompt_list_records_and_jumps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = self._make_window(settings)
            window.show()
            # 让两条提示词相隔较远，才能验证「跳转」确实改变了滚动位置
            for index in range(30):
                window._chat_view.append_step(f"— 第 {index} 步 —", "x" * 20)

            first = window._chat_view.append_message("user", "第一条提示词内容")
            window._prompt_list.add_prompt("第一条提示词内容", first)
            for index in range(40):
                window._chat_view.append_step(f"— 中段 {index} 步 —", "x" * 20)

            second = window._chat_view.append_message("user", "第二条提示词\n带换行的内容")
            window._prompt_list.add_prompt("第二条提示词\n带换行的内容", second)
            for index in range(10):
                window._chat_view.append_step(f"— 尾部 {index} 步 —", "x" * 20)

            self.assertEqual(window._prompt_list.count(), 2)
            self.assertEqual(window._prompt_list.item_mark(0), first)
            self.assertNotEqual(first, second)

            # 列表里折叠成单行，悬停能看到全文
            item = window._prompt_list._list.item(1)
            self.assertNotIn("\n", item.text())
            self.assertEqual(item.toolTip(), "第二条提示词\n带换行的内容")

            bar = window._chat_view.verticalScrollBar()
            bar.setValue(bar.maximum())
            before = bar.value()
            # 模拟点击列表首项，验证信号确实接到了跳转槽
            window._prompt_list._list.itemClicked.emit(window._prompt_list._list.item(0))
            self.assertLess(bar.value(), before)
            self.assertTrue(window._chat_view.extraSelections())

            window._clear_history()
            self.assertEqual(window._prompt_list.count(), 0)
            self.assertEqual(window._chat_view.toPlainText(), "")
            self.assertFalse(window._chat_view.scroll_to_mark(first))
            window.close()

    def test_approval_panel_waits_for_explicit_choice(self) -> None:
        """卡片出现本身不代表放行；没有点击就没有结论。"""
        request = ApprovalRequest(
            tool="run_command",
            args={"command": "rm -rf build"},
            title="rm -rf build",
            detail='{"command": "rm -rf build"}',
        )
        panel = ApprovalPanel()
        decisions = []
        panel.resolved.connect(lambda rid, decision: decisions.append((rid, decision)))

        panel.set_request(request)
        self.assertEqual(panel.pending_request_id(), request.request_id)
        self.assertFalse(panel.isHidden())
        self.assertEqual(decisions, [])
        # 提示语按运行时真实工具名匹配，而不是笼统的兜底文案
        self.assertIn("shell 命令", panel._hint_label.text())

        panel._reject_button.click()
        self.assertEqual(panel.pending_request_id(), "")
        self.assertTrue(panel.isHidden())
        self.assertEqual(decisions[0][0], request.request_id)
        self.assertFalse(decisions[0][1].allowed)

    def test_approval_panel_allow_remembers(self) -> None:
        request = ApprovalRequest(
            tool="edit_file",
            args={"path": "a.py"},
            title="a.py · 1 行 → 2 行",
            detail="{}",
        )
        panel = ApprovalPanel()
        decisions = []
        panel.resolved.connect(lambda rid, decision: decisions.append(decision))
        panel.set_request(request)
        panel._remember.setChecked(True)
        panel._allow_button.click()

        self.assertTrue(decisions[0].allowed)
        self.assertTrue(decisions[0].remember)

    def test_approval_panel_shows_in_input_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = self._make_window(settings)
            window.show()
            request = ApprovalRequest(
                tool="edit_file",
                args={"path": "a.py"},
                title="a.py · 1 行 → 2 行",
                detail="{}",
            )
            window._prompt_input.show_approval(request)
            self.assertTrue(window._prompt_input._approval.isVisible())
            self.assertEqual(window._prompt_input.pending_request_id(), request.request_id)

            # 超时之类的失效通知只收对应那张卡
            window._on_approval_expired("other-request", "等待确认超时")
            self.assertTrue(window._prompt_input.has_pending_approval())
            window._on_approval_expired(request.request_id, "等待确认超时")
            self.assertFalse(window._prompt_input.has_pending_approval())
            window.close()

    def test_context_bar_reflects_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = self._make_window(settings)
            window.show()

            bar = window._prompt_input._context_bar
            self.assertEqual(bar.value(), 0)

            window._prompt_input.set_context_usage(64_000, 128_000)
            self.assertEqual(bar.value(), 50)
            self.assertIn("50%", bar.toolTip())
            self.assertIn("64,000", bar.toolTip())

            window._prompt_input.set_context_usage(120_000, 128_000)
            self.assertEqual(bar.value(), 94)

            window._prompt_input.reset_context()
            self.assertEqual(bar.value(), 0)
            window.close()

    def test_provider_context_window(self) -> None:
        config = ProviderConfig(
            id="p", type="openai_compatible", values={"name": "x"}
        )
        self.assertEqual(config.context_window, 128_000)
        config.values["context_window"] = "64000"
        self.assertEqual(config.context_window, 64_000)
        config.values["context_window"] = "abc"
        self.assertEqual(config.context_window, 128_000)
        config.values["context_window"] = "0"
        self.assertEqual(config.context_window, 128_000)

    def test_workspace_root_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            settings.sync()
            reloaded = Settings(
                QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            )
            self.assertEqual(reloaded.workspace_root, tmp)

    def test_workspace_combo_lists_dir_and_pick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = self._make_window(settings)
            combo = window._workspace_combo
            current = str(Path(tmp).resolve())
            # 当前目录就是选中项，最后一项固定是「选择目录…」
            self.assertEqual(combo.currentData(), current)
            self.assertEqual(combo.itemData(0), current)
            self.assertEqual(combo.itemText(combo.count() - 1), "选择目录…")
            self.assertEqual(combo.toolTip(), current)
            window.close()

    def test_workspace_switch_records_history(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            settings = self._make_settings(first)
            window = self._make_window(settings)
            first_path = str(Path(first).resolve())
            second_path = str(Path(second).resolve())

            # 通过「选择目录…」切到新目录：新目录顶到最前，旧目录留在历史
            with patch(
                "app.ui.main_window.QFileDialog.getExistingDirectory",
                return_value=second,
            ):
                window._choose_workspace()
            self.assertEqual(str(window._workspace_root), second_path)
            self.assertEqual(window._workspace_combo.currentData(), second_path)
            self.assertEqual(settings.workspace_history[:2], [second_path, first_path])
            self.assertEqual(window._workspace_combo.itemData(0), second_path)
            self.assertEqual(
                [window._workspace_combo.itemData(i) for i in range(2)],
                [second_path, first_path],
            )

            # 从下拉框切回旧目录
            index = window._workspace_combo.findData(first_path)
            self.assertGreater(index, 0)
            window._on_workspace_activated(index)
            self.assertEqual(str(window._workspace_root), first_path)
            self.assertEqual(window._workspace_combo.currentData(), first_path)
            self.assertEqual(settings.workspace_history[0], first_path)

            # 点「选择目录…」后取消：选中项回到当前目录
            with patch(
                "app.ui.main_window.QFileDialog.getExistingDirectory",
                return_value="",
            ):
                window._on_workspace_activated(window._workspace_combo.count() - 1)
            self.assertEqual(window._workspace_combo.currentData(), first_path)
            window.close()

    def test_missing_workspace_removed_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as gone:
            gone_path = str(Path(gone).resolve())
            settings = self._make_settings(tmp)
            settings.workspace_history = [gone_path]
            settings.sync()

            window = self._make_window(settings)
            combo = window._workspace_combo
            index = combo.findData(gone_path)
            self.assertGreater(index, 0)

            os.rmdir(gone)
            window._on_workspace_activated(index)

            self.assertEqual(combo.findData(gone_path), -1)
            self.assertNotIn(gone_path, settings.workspace_history)
            # 工作区没有被切走
            self.assertEqual(str(window._workspace_root), str(Path(tmp).resolve()))
            self.assertEqual(combo.currentData(), str(Path(tmp).resolve()))
            window.close()

    def test_workspace_history_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            history = [str(Path(tmp).resolve()), "C:/somewhere/else"]
            settings.workspace_history = history
            settings.sync()
            reloaded = Settings(
                QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            )
            self.assertEqual(reloaded.workspace_history, history)


if __name__ == "__main__":
    unittest.main(verbosity=2)
