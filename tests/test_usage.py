"""每轮模型 token 用量：读取、累加、界面展示与会话持久化。

运行：.venv\\Scripts\\python.exe tests\\test_usage.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.agent import AgentRuntime, Workspace
from app.agent import events as E
from app.agent.usage import (
    APPROXIMATE_MARK,
    TokenUsage,
    as_usage,
    compact_tokens,
    message_usage,
)
from app.config import Settings
from app.core import ProviderConfig, ProviderManager, ProviderStore
from app.ui import MainWindow
from app.ui.sessions import Session
from store_support import workspace_store_factory
from app.ui.widgets import PromptInput
from test_agent_loop import AllowAll, ScriptedChatModel, tool_call


class MessageUsageTest(unittest.TestCase):
    def test_reads_usage_metadata(self) -> None:
        message = AIMessage(
            content="hi",
            usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
        )
        self.assertEqual(message_usage(message), (120, 8))

    def test_reads_openai_token_usage(self) -> None:
        message = AIMessage(
            content="hi",
            response_metadata={
                "token_usage": {"prompt_tokens": 90, "completion_tokens": 12}
            },
        )
        self.assertEqual(message_usage(message), (90, 12))

    def test_reads_anthropic_usage(self) -> None:
        message = AIMessage(
            content="hi",
            response_metadata={"usage": {"input_tokens": 70, "output_tokens": 5}},
        )
        self.assertEqual(message_usage(message), (70, 5))

    def test_missing_usage_returns_none(self) -> None:
        self.assertIsNone(message_usage(AIMessage(content="hi")))
        # 只有一半字段算作没给，别把 0 当成「输出 0 token」
        partial = AIMessage(content="hi", response_metadata={"usage": {"input_tokens": 7}})
        self.assertIsNone(message_usage(partial))

    def test_non_numeric_usage_is_ignored(self) -> None:
        # usage_metadata 由 LangChain 校验过类型，脏数据只会出现在 response_metadata 里
        message = AIMessage(
            content="hi",
            response_metadata={"usage": {"input_tokens": "many", "output_tokens": 3}},
        )
        self.assertIsNone(message_usage(message))


class TokenUsageTest(unittest.TestCase):
    def test_accumulates_and_keeps_approximate_flag(self) -> None:
        usage = TokenUsage()
        usage.add(100, 10)
        self.assertFalse(usage.approximate)
        usage.add(50, 5, approximate=True)
        self.assertEqual((usage.input_tokens, usage.output_tokens), (150, 15))
        self.assertEqual(usage.total, 165)
        self.assertTrue(usage.approximate)

    def test_round_trip(self) -> None:
        usage = TokenUsage(12, 3, True)
        restored = as_usage(usage.to_dict())
        self.assertEqual((restored.input_tokens, restored.output_tokens), (12, 3))
        self.assertTrue(restored.approximate)
        self.assertIsNone(as_usage({"input_tokens": "x"}))

    def test_compact_formatting(self) -> None:
        self.assertEqual(compact_tokens(0), "0")
        self.assertEqual(compact_tokens(999), "999")
        self.assertEqual(compact_tokens(1000), "1k")
        self.assertEqual(compact_tokens(1234), "1.2k")
        self.assertEqual(compact_tokens(12_345), "12.3k")
        self.assertEqual(compact_tokens(1_500_000), "1.5m")


class RuntimeUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "notes.txt").write_text("hello\n", encoding="utf-8")
        self.workspace = Workspace(self.root)
        self.events: list = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_provider_usage_is_summed_over_calls(self) -> None:
        def reply(content: str, call_id: str | None = None) -> AIMessage:
            message = AIMessage(
                content=content,
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            )
            if call_id:
                message.tool_calls = [
                    {"name": "read_file", "args": {"path": "notes.txt"}, "id": call_id}
                ]
            return message

        runtime = AgentRuntime(
            ScriptedChatModel(responses=[reply("", "c1"), reply("读完了。")]),
            self.workspace,
            AllowAll(),
        )
        runtime.run("看看 notes", [], self.events.append)

        # 两次调用各 100/20，合计 200/40；这是 provider 的真实数字，不是估算
        self.assertEqual((runtime.usage.input_tokens, runtime.usage.output_tokens), (200, 40))
        self.assertFalse(runtime.usage.approximate)

        usages = [event for event in self.events if event.kind == E.USAGE]
        self.assertEqual(len(usages), 2)
        self.assertEqual((usages[0].input_tokens, usages[0].output_tokens), (100, 20))
        self.assertEqual((usages[-1].input_tokens, usages[-1].output_tokens), (200, 40))

    def test_falls_back_to_estimate_when_provider_is_silent(self) -> None:
        runtime = AgentRuntime(
            ScriptedChatModel(responses=[AIMessage(content="你好，有什么可以帮你？")]),
            self.workspace,
            AllowAll(),
        )
        runtime.run("打个招呼", [], self.events.append)

        self.assertTrue(runtime.usage.approximate)
        self.assertGreater(runtime.usage.input_tokens, 0)
        self.assertGreater(runtime.usage.output_tokens, 0)
        usage_event = [event for event in self.events if event.kind == E.USAGE][-1]
        self.assertTrue(usage_event.approximate)

    def test_usage_is_reset_between_runs(self) -> None:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="ok",
                    usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                )
            ]
        )
        runtime = AgentRuntime(model, self.workspace, AllowAll())
        runtime.run("第一次", [], self.events.append)
        self.assertEqual(runtime.usage.total, 12)

        runtime.run("第二次", [], self.events.append)
        # 新一轮只算自己的，不带着上一轮的账
        self.assertEqual(runtime.usage.total, 12)

    def test_cancelled_before_call_reports_no_usage(self) -> None:
        runtime = AgentRuntime(
            ScriptedChatModel(responses=[AIMessage(content="不该被调用")]),
            self.workspace,
            AllowAll(),
        )
        runtime.cancel()
        runtime.run("停", [], self.events.append)
        self.assertEqual(runtime.usage.total, 0)
        self.assertFalse([event for event in self.events if event.kind == E.USAGE])


class PromptInputTokenLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_placeholder_before_any_usage(self) -> None:
        widget = PromptInput()
        self.assertEqual(widget.token_label_text(), "↑ —  ↓ —")
        self.assertIn("累计", widget._token_label.toolTip())

    def test_shows_round_usage_with_session_total_in_tooltip(self) -> None:
        widget = PromptInput()
        widget.set_token_usage(12_345, 1_800, session_in=20_000, session_out=2_500)
        self.assertEqual(widget.token_label_text(), "↑ 12.3k  ↓ 1.8k")
        tip = widget._token_label.toolTip()
        self.assertIn("本轮：输入 12,345 · 输出 1,800", tip)
        self.assertIn("累计：输入 20,000 · 输出 2,500", tip)

    def test_approximate_is_marked(self) -> None:
        widget = PromptInput()
        widget.set_token_usage(900, 100, approximate=True)
        self.assertTrue(widget.token_label_text().startswith(f"↑ {APPROXIMATE_MARK}"))
        self.assertIn("估算", widget._token_label.toolTip())

    def test_label_sits_left_of_context_bar(self) -> None:
        widget = PromptInput()
        widget.resize(800, 200)
        widget.show()
        self.app.processEvents()
        label_x = widget._token_label.mapTo(widget, widget._token_label.rect().topLeft()).x()
        bar_x = widget._context_bar.mapTo(widget, widget._context_bar.rect().topLeft()).x()
        self.assertLess(label_x, bar_x)
        widget.close()


class SessionUsagePersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_session_keeps_cumulative_and_last_round(self) -> None:
        session = Session.create(now=1700000000.0, workspace=str(self.root))
        session.tokens_in = 500
        session.tokens_out = 60
        session.last_tokens_in = 100
        session.last_tokens_out = 10

        restored = Session.from_dict(session.to_dict())
        assert restored is not None
        self.assertEqual(restored.tokens_in, 500)
        self.assertEqual(restored.last_tokens_out, 10)

    def test_missing_or_broken_fields_default_to_zero(self) -> None:
        restored = Session.from_dict(
            {"id": "s1", "tokens_in": "很多", "last_tokens_out": -5}
        )
        assert restored is not None
        self.assertEqual(restored.tokens_in, 0)
        self.assertEqual(restored.last_tokens_out, 0)

    def _window(self) -> MainWindow:
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
        return MainWindow(
            settings, ProviderManager(settings), workspace_store_factory(self.root)
        )

    def test_round_usage_accumulates_into_session(self) -> None:
        window = self._window()
        session = window._active_session()

        usage = TokenUsage(300, 40)
        runtime = AgentRuntime(
            ScriptedChatModel(responses=[AIMessage(content="ok")]),
            Workspace(self.root),
            AllowAll(),
        )
        runtime._usage = usage

        window._record_usage(runtime)
        self.assertEqual((session.tokens_in, session.tokens_out), (300, 40))
        self.assertEqual(window._prompt_input.token_label_text(), "↑ 300  ↓ 40")

        window._record_usage(runtime)
        # 再来一轮同规模的，累计翻倍，标签显示的仍是这一轮
        self.assertEqual((session.tokens_in, session.tokens_out), (600, 80))
        self.assertEqual(window._prompt_input.token_label_text(), "↑ 300  ↓ 40")
        self.assertIn("累计：输入 600", window._prompt_input._token_label.toolTip())
        window.close()

    def test_switching_session_restores_its_numbers(self) -> None:
        window = self._window()
        first = window._active_session()
        first.tokens_in, first.tokens_out = 1_000, 90
        first.last_tokens_in, first.last_tokens_out = 400, 30

        window._on_new_session()
        second = window._active_session()
        self.assertNotEqual(second.id, first.id)
        self.assertEqual(window._prompt_input.token_label_text(), "↑ —  ↓ —")

        window._switch_session(first)
        self.assertEqual(window._prompt_input.token_label_text(), "↑ 400  ↓ 30")
        self.assertIn("累计：输入 1,000", window._prompt_input._token_label.toolTip())
        window.close()

    def test_clearing_history_resets_token_counts(self) -> None:
        window = self._window()
        session = window._active_session()
        session.tokens_in = session.tokens_out = 800
        session.last_tokens_in = session.last_tokens_out = 200
        window._prompt_input.set_token_usage(200, 200, session_in=800, session_out=800)

        window._clear_history()

        self.assertEqual((session.tokens_in, session.tokens_out), (0, 0))
        self.assertEqual(window._prompt_input.token_label_text(), "↑ —  ↓ —")
        window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
