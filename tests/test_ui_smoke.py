"""界面装配冒烟测试：不弹真实窗口，也不需要模型。

运行：.venv\\Scripts\\python.exe tests\\test_ui_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.agent import ApprovalRequest
from app.config import Settings
from app.constants import APP_NAME
from app.core import ProviderConfig, ProviderManager
from app.ui import MainWindow
from app.ui.dialogs import ApprovalDialog


class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_settings(self, directory: str) -> Settings:
        qsettings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
        settings = Settings(qsettings)
        settings.workspace_root = directory
        return settings

    def test_window_builds_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = MainWindow(settings, ProviderManager(settings))
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
            window = MainWindow(settings, ProviderManager(settings))
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

    def test_approval_dialog_defaults_to_deny(self) -> None:
        request = ApprovalRequest(
            tool="run_command",
            args={"command": "rm -rf build"},
            title="rm -rf build",
            detail='{"command": "rm -rf build"}',
        )
        dialog = ApprovalDialog(request)
        self.assertFalse(dialog.decision().allowed)
        dialog.close()

    def test_context_bar_reflects_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            window = MainWindow(settings, ProviderManager(settings))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
