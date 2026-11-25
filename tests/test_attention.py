"""任务栏闪烁测试：offscreen 运行，不会真的闪窗口。

运行：.venv\\Scripts\\python.exe tests\\test_attention.py
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
from PyQt6.QtWidgets import QApplication, QWidget

from app.agent import ApprovalRequest
from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow, attention, sound
from store_support import workspace_store_factory


class FakeWindow:
    """attention 只用到 isVisible / isMinimized，测试里不必真的开窗口。"""

    def __init__(self, visible: bool = True, minimized: bool = False) -> None:
        self.visible = visible
        self.minimized = minimized

    def isVisible(self) -> bool:
        return self.visible

    def isMinimized(self) -> bool:
        return self.minimized


class AttentionModuleTest(unittest.TestCase):
    """何时闪、何时别闪。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_skips_when_user_is_looking(self) -> None:
        window = FakeWindow()
        with patch.object(attention, "_is_foreground", return_value=True), \
                patch.object(attention, "_flash_windows") as native:
            self.assertFalse(attention.flash(window))
        native.assert_not_called()

    def test_flashes_window_in_background(self) -> None:
        window = FakeWindow()
        with patch.object(attention, "_is_foreground", return_value=False), \
                patch.object(attention, "_WINDOWS", True), \
                patch.object(attention, "_flash_windows", return_value=True) as native:
            self.assertTrue(attention.flash(window))
        native.assert_called_once_with(
            window, attention._FLASHW_ALL | attention._FLASHW_TIMERNOFG
        )

    def test_hidden_window_is_skipped(self) -> None:
        """还没显示出来的窗口没有任务栏按钮，闪了也没人看见。"""
        window = FakeWindow(visible=False)
        with patch.object(attention, "_is_foreground", return_value=False), \
                patch.object(attention, "_flash_windows") as native:
            self.assertFalse(attention.flash(window))
        native.assert_not_called()

    def test_stop_sends_stop_flag(self) -> None:
        window = FakeWindow()
        with patch.object(attention, "_WINDOWS", True), \
                patch.object(attention, "_flash_windows") as native:
            attention.stop(window)
        native.assert_called_once_with(window, attention._FLASHW_STOP)

    def test_minimized_window_counts_as_not_looking(self) -> None:
        """最小化时应用可能仍算 Active，但那正是最该闪的时候。"""
        window = FakeWindow(minimized=True)
        with patch.object(attention, "_app_is_active", return_value=True):
            self.assertFalse(attention._is_foreground(window))

    def test_active_window_counts_as_looking(self) -> None:
        window = FakeWindow()
        with patch.object(attention, "_app_is_active", return_value=True):
            self.assertTrue(attention._is_foreground(window))
        with patch.object(attention, "_app_is_active", return_value=False):
            self.assertFalse(attention._is_foreground(window))

    def test_other_platform_asks_qt(self) -> None:
        window = FakeWindow()
        with patch.object(attention, "_is_foreground", return_value=False), \
                patch.object(attention, "_WINDOWS", False), \
                patch.object(attention, "_alert_qt", return_value=True) as alert:
            self.assertTrue(attention.flash(window))
        alert.assert_called_once_with(window)

    def test_native_call_is_safe(self) -> None:
        """真去调一次底层：(离屏)窗口拿不到有效句柄，也不能抛异常。"""
        window = QWidget()
        window.show()
        try:
            result = attention._flash_windows(window, attention._FLASHW_ALL)
            self.assertIsInstance(result, bool)
            attention._flash_windows(window, attention._FLASHW_STOP)
        finally:
            window.close()


class MainWindowAttentionTest(unittest.TestCase):
    """会话完成 / 等待确认 / 出错 / 确认失效都要招手。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        settings = Settings(
            QSettings(str(root / "s.ini"), QSettings.Format.IniFormat)
        )
        settings.workspace_root = str(root)
        config = ProviderConfig(
            id="p1",
            type="openai_compatible",
            values={"name": "fake", "api_key": "sk-test", "model": "fake-model"},
        )
        settings.providers_json = ProviderStore.dumps([config])
        settings.active_provider_id = "p1"
        self.window = MainWindow(
            settings, ProviderManager(settings), workspace_store_factory(root)
        )

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def _make_request(self) -> ApprovalRequest:
        return ApprovalRequest(
            tool="run_command",
            args={"command": "pytest -q"},
            title="pytest -q",
            detail='{"command": "pytest -q"}',
        )

    def test_approval_request_flashes(self) -> None:
        with patch.object(attention, "flash", return_value=True) as flashed, \
                patch.object(sound, "play_confirmation", return_value=True):
            self.window._on_approval_requested(self._make_request())
        flashed.assert_called_once_with(self.window)

    def test_run_done_flashes(self) -> None:
        with patch.object(attention, "flash", return_value=True) as flashed, \
                patch.object(sound, "play_completion", return_value=True):
            self.window._on_run_done([], None)
        flashed.assert_called_once_with(self.window)

    def test_run_failed_flashes(self) -> None:
        with patch.object(attention, "flash", return_value=True) as flashed:
            self.window._on_run_failed("boom", None)
        flashed.assert_called_once_with(self.window)

    def test_expired_approval_stops_flashing(self) -> None:
        with patch.object(attention, "stop") as stopped:
            self.window._on_approval_expired("rid", "等待确认超时")
        stopped.assert_called_once_with(self.window)


if __name__ == "__main__":
    unittest.main(verbosity=2)
