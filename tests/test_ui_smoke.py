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
from app.core import ProviderManager
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
