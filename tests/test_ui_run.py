"""端到端测试：真跑一遍窗口 → 工作线程 → 审批 → 回灌 → 渲染。

模型仍是假的，但线程、信号、输入区确认卡、事件渲染都是真的。
运行：.venv\\Scripts\\python.exe tests\\test_ui_run.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow
from app.ui.sessions import SessionStore
from test_agent_loop import ScriptedChatModel, tool_call

ALLOWED = {"value": True}


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "hello.py").write_text('print("hello")\n', encoding="utf-8")
        ALLOWED["value"] = True

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build_window(self, model: ScriptedChatModel) -> MainWindow:
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

        manager = ProviderManager(settings)
        manager.build_model = lambda cfg: model  # 跳过真实 provider
        store = SessionStore(self.root / "sessions.json")
        window = MainWindow(settings, manager, store)
        window.show()
        return window

    def wait_for_idle(self, window: MainWindow, timeout: float = 20.0) -> None:
        """跑事件循环直到任务结束；途中遇到确认卡就替用户点一下。"""
        answered: set[str] = set()
        deadline = time.monotonic() + timeout
        while window._active_handles and time.monotonic() < deadline:
            self.app.processEvents()
            request_id = window._prompt_input.pending_request_id()
            if request_id and request_id not in answered:
                answered.add(request_id)
                panel = window._prompt_input._approval
                if ALLOWED["value"]:
                    panel._allow_button.click()
                else:
                    panel._reject_button.click()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window._active_handles, "任务在超时前没有结束")
        self.assertFalse(window._prompt_input.has_pending_approval(), "确认卡没有收起")

    def test_full_loop_with_approved_edit(self) -> None:
        model = ScriptedChatModel(
            responses=[
                tool_call("read_file", {"path": "hello.py"}, "c1"),
                tool_call(
                    "edit_file",
                    {"path": "hello.py", "old_string": '"hello"', "new_string": '"hi"'},
                    "c2",
                ),
                AIMessage(content="已把输出改成 hi。"),
            ]
        )
        window = self.build_window(model)
        window._on_submit("把 hello 改成 hi")
        self.wait_for_idle(window)

        self.assertEqual((self.root / "hello.py").read_text(encoding="utf-8"), 'print("hi")\n')
        transcript = window._chat_view.toPlainText()
        self.assertIn("read_file", transcript)
        self.assertIn("edit_file", transcript)
        self.assertIn("已把输出改成 hi", transcript)
        self.assertTrue(window._history)
        # 提交的任务应登记到左侧提示词列表
        self.assertEqual(window._prompt_list.count(), 1)
        self.assertIsNotNone(window._prompt_list.item_mark(0))
        # 上下文进度条应随运行时事件更新
        self.assertGreater(window._prompt_input._context_bar.value(), 0)
        window.close()

    def test_denied_edit_leaves_file_intact(self) -> None:
        ALLOWED["value"] = False
        model = ScriptedChatModel(
            responses=[
                tool_call(
                    "edit_file",
                    {"path": "hello.py", "old_string": '"hello"', "new_string": '"nope"'},
                    "c1",
                ),
                AIMessage(content="那我不改了。"),
            ]
        )
        window = self.build_window(model)
        window._on_submit("改掉 hello")
        self.wait_for_idle(window)

        self.assertEqual((self.root / "hello.py").read_text(encoding="utf-8"), 'print("hello")\n')
        transcript = window._chat_view.toPlainText()
        self.assertIn("已拒绝", transcript)
        self.assertIn("那我不改了", transcript)
        window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
