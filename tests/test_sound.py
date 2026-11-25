"""任务完成提示音测试：不需要声卡，offscreen 运行。

运行：.venv\\Scripts\\python.exe tests\\test_sound.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
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
from store_support import workspace_store_factory


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
    """确认卡出现时应该响；命中「本会话已允许」而直接放行时不该响。"""

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
            settings, ProviderManager(settings), workspace_store_factory(self.root)
        )
        self.window.show()

        sound.reset()
        self.played: list[str] = []
        self._original_confirm = sound.play_confirmation
        sound.play_confirmation = lambda: self.played.append("confirm") or True

        from app.ui.agent_bridge import AgentBridge

        self.bridge = AgentBridge(self.window)
        self.bridge.approval_requested.connect(self.window._on_approval_requested)
        self.bridge.approval_expired.connect(self.window._on_approval_expired)
        # 真实流程里由 _on_submit 挂上，这里手工接上同一个对象
        self.window._bridge = self.bridge

    def tearDown(self) -> None:
        sound.play_confirmation = self._original_confirm
        self.window._bridge = None
        self.window.close()
        sound.reset()
        self._tmp.cleanup()

    def _make_request(self) -> ApprovalRequest:
        return ApprovalRequest(
            tool="run_command",
            args={"command": "pytest -q"},
            title="pytest -q",
            detail='{"command": "pytest -q"}',
        )

    def _start_request(self, request: ApprovalRequest):
        """在工作线程里发起审批（和 runtime 一样阻塞），等界面铺开确认卡。"""
        result: dict[str, ApprovalDecision] = {}
        thread = threading.Thread(
            target=lambda: result.update(decision=self.bridge.request(request)),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 5
        while (
            time.monotonic() < deadline
            and not self.window._prompt_input.has_pending_approval()
        ):
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(
            self.window._prompt_input.pending_request_id(), request.request_id
        )
        self.assertEqual(self.played, ["confirm"])
        return thread, result

    def test_sound_plays_when_panel_shown(self) -> None:
        request = self._make_request()
        thread, result = self._start_request(request)

        self.window._prompt_input._approval._allow_button.click()
        thread.join(timeout=5)
        self.assertTrue(result["decision"].allowed)
        self.assertFalse(self.window._prompt_input.has_pending_approval())

    def test_no_sound_when_already_remembered(self) -> None:
        """第二次同类操作直接放行、不铺确认卡，也就不该再响。"""
        first = self._make_request()
        thread, _ = self._start_request(first)
        panel = self.window._prompt_input._approval
        panel._remember.setChecked(True)
        panel._allow_button.click()
        thread.join(timeout=5)
        self.played.clear()

        # 第二次同类操作命中已记住的规则，直接返回、不出卡
        self.assertTrue(self.bridge.request(self._make_request()).allowed)
        self.assertEqual(self.played, [])
        self.assertFalse(self.window._prompt_input.has_pending_approval())


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
            settings, ProviderManager(settings), workspace_store_factory(self.root)
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
