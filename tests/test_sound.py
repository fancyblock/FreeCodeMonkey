"""任务完成提示音测试：不需要声卡，offscreen 运行。

运行：.venv\\Scripts\\python.exe tests\\test_sound.py
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

from app.agent import ApprovalDecision, ApprovalRequest
from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow, sound
from app.ui.sessions import SessionStore


class SoundModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        sound.reset()

    def tearDown(self) -> None:
        sound.reset()

    def test_sound_files_exist(self) -> None:
        for name, expected in (
            (sound.COMPLETION_SOUND, "mission_complete_strong_2s.mp3"),
            (sound.CONFIRM_SOUND, "question_confirmation_strong_1_5s.mp3"),
        ):
            path = sound.sound_path(name)
            self.assertTrue(path.is_file(), f"缺少音效文件：{path}")
            self.assertEqual(path.name, expected)
            self.assertEqual(path.parent.name, "sound")

    def test_is_available(self) -> None:
        self.assertTrue(sound.is_available(sound.COMPLETION_SOUND))
        self.assertTrue(sound.is_available(sound.CONFIRM_SOUND))

    def test_play_returns_true_and_does_not_raise(self) -> None:
        self.assertTrue(sound.play_completion())
        self.assertTrue(sound.play_confirmation())

    def test_play_twice_resets_position(self) -> None:
        """第二次播放要把进度拨回开头，否则上一轮停在末尾就没声了。"""
        sound.play_completion()
        player = sound._ensure_player(sound.COMPLETION_SOUND)
        player.setPosition(1000)
        sound.play_completion()
        self.assertEqual(player.position(), 0)

    def test_missing_file_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sound, "_SOUND_DIR", Path(tmp)):
                self.assertFalse(sound.is_available(sound.COMPLETION_SOUND))
                self.assertFalse(sound.play_completion())
                self.assertFalse(sound.play_confirmation())

    def test_unavailable_backend_is_silent(self) -> None:
        """模拟没装 QtMultimedia：调用方不应该看到异常。"""
        sound.reset()
        sound._broken.update({sound.COMPLETION_SOUND, sound.CONFIRM_SOUND})
        try:
            self.assertFalse(sound.play_completion())
            self.assertFalse(sound.play_confirmation())
        finally:
            sound.reset()

    def test_player_is_singleton(self) -> None:
        first = sound._ensure_player(sound.COMPLETION_SOUND)
        second = sound._ensure_player(sound.COMPLETION_SOUND)
        self.assertIs(first, second)

    def test_each_sound_has_its_own_player(self) -> None:
        """两种音效各有播放器，同时触发时不会互相打断。"""
        completion = sound._ensure_player(sound.COMPLETION_SOUND)
        confirm = sound._ensure_player(sound.CONFIRM_SOUND)
        self.assertIsNot(completion, confirm)
        self.assertTrue(sound.play_completion())
        self.assertTrue(sound.play_confirmation())


class ApprovalSoundTest(unittest.TestCase):
    """弹出确认框时应该响；命中「本会话已允许」而直接放行时不该响。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import app.ui.agent_bridge as bridge_module

        sound.reset()
        self.played: list[str] = []
        self._original_dialog = bridge_module.ApprovalDialog
        self._original_confirm = sound.play_confirmation
        sound.play_confirmation = lambda: self.played.append("confirm") or True
        self.bridge = bridge_module.AgentBridge()

    def tearDown(self) -> None:
        import app.ui.agent_bridge as bridge_module

        bridge_module.ApprovalDialog = self._original_dialog
        sound.play_confirmation = self._original_confirm
        sound.reset()

    def _make_request(self) -> ApprovalRequest:
        return ApprovalRequest(
            tool="run_command",
            args={"command": "pytest -q"},
            title="pytest -q",
            detail='{"command": "pytest -q"}',
        )

    def _stub_dialog(self, allowed: bool) -> None:
        import app.ui.agent_bridge as bridge_module

        class Stub:
            def __init__(self, request, parent=None) -> None:
                self.request = request

            def exec(self) -> int:
                return 1

            def decision(self) -> ApprovalDecision:
                return ApprovalDecision(allowed=allowed, reason="stub")

        bridge_module.ApprovalDialog = Stub

    def test_sound_plays_when_dialog_shown(self) -> None:
        self._stub_dialog(allowed=True)
        self.assertTrue(self.bridge.request(self._make_request()).allowed)
        self.assertEqual(self.played, ["confirm"])

    def test_no_sound_when_already_remembered(self) -> None:
        """第二次同类操作直接放行、不弹窗，也就不该再响。"""
        self._stub_dialog(allowed=True)
        first = ApprovalRequest(
            tool="run_command",
            args={"command": "a"},
            title="a",
            detail="{}",
        )
        # 让第一次的结论带上「记住」
        import app.ui.agent_bridge as bridge_module

        class RememberingStub:
            def __init__(self, request, parent=None) -> None:
                self.request = request

            def exec(self) -> int:
                return 1

            def decision(self) -> ApprovalDecision:
                return ApprovalDecision(allowed=True, remember=True, reason="stub")

        bridge_module.ApprovalDialog = RememberingStub
        self.bridge.request(first)
        self.played.clear()

        # 第二次同类操作命中已记住的规则，直接返回、不弹窗
        second = ApprovalRequest(
            tool="run_command",
            args={"command": "b"},
            title="b",
            detail="{}",
        )
        self.assertTrue(self.bridge.request(second).allowed)
        self.assertEqual(self.played, [])


class CompletionSoundHookTest(unittest.TestCase):
    """任务结束时应该响，失败或手动停止时不应该响。"""

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
        self.played: list[bool] = []

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def _patch_sound(self):
        return patch.object(
            sound, "play_completion", lambda: self.played.append(True)
        )

    def test_run_done_plays_sound(self) -> None:
        with self._patch_sound():
            self.window._on_run_done([], None)
        self.assertEqual(self.played, [True])

    def test_run_failed_does_not_play(self) -> None:
        with self._patch_sound():
            self.window._on_run_failed("boom", None)
        self.assertEqual(self.played, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
