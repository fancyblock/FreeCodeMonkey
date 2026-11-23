"""离线验证 agent 循环与工具行为。

不依赖任何真实模型：用一个按脚本返回 AIMessage 的假模型驱动循环。
运行：.venv\\Scripts\\python.exe tests\\test_agent_loop.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.agent import AgentRuntime, ApprovalDecision, ApprovalRequest, Workspace
from app.agent import events as E


class ScriptedChatModel(BaseChatModel):
    """按顺序返回预设回复的假模型。"""

    responses: list = Field(default_factory=list)
    calls: int = 0
    received_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def _identifying_params(self) -> dict:
        return {}

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - 与基类签名保持一致
        self.received_tools.append(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])


class AllowAll:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(req)
        return ApprovalDecision(allowed=True, reason="test")


class DenyAll:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(req)
        return ApprovalDecision(allowed=False, reason="测试拒绝")


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class AgentLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text(
            'def greet(name):\n    return f"hello {name}"\n\n\n'
            'def farewell():\n    return "bye"\n',
            encoding="utf-8",
        )
        (self.root / "notes.txt").write_text("TODO: refactor greet\n", encoding="utf-8")
        self.workspace = Workspace(self.root)
        self.events: list = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_agent(self, responses, approval=None, max_steps=10, cancel=False):
        model = ScriptedChatModel(responses=responses)
        runtime = AgentRuntime(model, self.workspace, approval or AllowAll(), max_steps=max_steps)
        if cancel:
            runtime.cancel()
        result = runtime.run("测试任务", [], self.events.append)
        return model, result

    # ---- 基础 ----
    def test_plain_reply_finishes_without_tools(self) -> None:
        model, result = self.run_agent([AIMessage(content="你好，有什么可以帮你？")])
        self.assertEqual(model.calls, 1)
        self.assertEqual(len(result), 2)  # HumanMessage + AIMessage
        kinds = [event.kind for event in self.events]
        self.assertEqual(kinds, [E.STEP, E.DONE])
        self.assertIn("你好", self.events[-1].text)

    def test_tools_are_registered(self) -> None:
        model, _ = self.run_agent([AIMessage(content="ok")])
        self.assertEqual(len(model.received_tools), 1)
        names = [schema["function"]["name"] for schema in model.received_tools[0]]
        self.assertEqual(names, ["read_file", "list_dir", "grep", "edit_file", "run_command"])

    # ---- 完整闭环 ----
    def test_read_then_edit_then_confirm(self) -> None:
        approval = AllowAll()
        _, result = self.run_agent(
            [
                tool_call("read_file", {"path": "src/app.py"}, "c1"),
                tool_call(
                    "edit_file",
                    {
                        "path": "src/app.py",
                        "old_string": 'return f"hello {name}"',
                        "new_string": 'return f"hi {name}"',
                    },
                    "c2",
                ),
                AIMessage(content="已把问候语改成 hi。"),
            ],
            approval,
        )

        content = (self.root / "src" / "app.py").read_text(encoding="utf-8")
        self.assertIn('f"hi {name}"', content)
        self.assertNotIn('f"hello {name}"', content)

        # 只有 edit_file 需要审批，read_file 不打扰用户
        self.assertEqual([req.tool for req in approval.requests], ["edit_file"])
        self.assertIn("1 行 → 1 行", approval.requests[0].title)

        kinds = [event.kind for event in self.events]
        self.assertEqual(kinds.count(E.TOOL_CALL), 2)
        self.assertEqual(kinds.count(E.TOOL_RESULT), 2)
        self.assertEqual(kinds[-1], E.DONE)
        self.assertEqual(result[-1].content, "已把问候语改成 hi。")

        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertEqual(len(tool_messages), 2)
        self.assertIn("return f", tool_messages[0].content)

    def test_create_new_file_via_edit_file(self) -> None:
        self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {"path": "new/module.py", "old_string": "", "new_string": "VALUE = 1\n"},
                    "c1",
                ),
                AIMessage(content="已创建。"),
            ]
        )
        created = self.root / "new" / "module.py"
        self.assertTrue(created.exists())
        self.assertEqual(created.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_run_command_reports_exit_code(self) -> None:
        _, result = self.run_agent(
            [
                tool_call("run_command", {"command": "echo hello-agent"}, "c1"),
                AIMessage(content="done"),
            ]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("退出码：0", tool_messages[0].content)
        self.assertIn("hello-agent", tool_messages[0].content)

    def test_grep_finds_matches(self) -> None:
        _, result = self.run_agent(
            [tool_call("grep", {"pattern": "refactor", "glob": "*.txt"}, "c1"), AIMessage(content="ok")]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("notes.txt:1:", tool_messages[0].content)

    def test_list_dir_skips_noise(self) -> None:
        (self.root / "__pycache__").mkdir()
        _, result = self.run_agent(
            [tool_call("list_dir", {"depth": 2}, "c1"), AIMessage(content="ok")]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("src/", tool_messages[0].content)
        self.assertNotIn("__pycache__", tool_messages[0].content)

    # ---- 边界 ----
    def test_path_escape_is_rejected(self) -> None:
        _, result = self.run_agent(
            [
                tool_call("read_file", {"path": "../../etc/passwd"}, "c1"),
                AIMessage(content="ok"),
            ]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("超出工作区", tool_messages[0].content)
        self.assertTrue(self.events[2].ok is False)

    def test_ambiguous_edit_fails_clearly(self) -> None:
        _, result = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {"path": "src/app.py", "old_string": "def ", "new_string": "def  "},
                    "c1",
                ),
                AIMessage(content="ok"),
            ]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("出现 2 次", tool_messages[0].content)
        # 出错时文件必须保持原样
        self.assertIn('def greet', (self.root / "src" / "app.py").read_text(encoding="utf-8"))

    def test_missing_file_hint_for_creation(self) -> None:
        _, result = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {"path": "ghost.py", "old_string": "x", "new_string": "y"},
                    "c1",
                ),
                AIMessage(content="ok"),
            ]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("old_string 设为空字符串", tool_messages[0].content)

    def test_unknown_tool_is_reported(self) -> None:
        _, result = self.run_agent(
            [tool_call("delete_everything", {}, "c1"), AIMessage(content="ok")]
        )
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("不存在", tool_messages[0].content)
        self.assertIn("run_command", tool_messages[0].content)

    # ---- 审批拒绝 ----
    def test_denied_edit_does_not_touch_file(self) -> None:
        approval = DenyAll()
        _, result = self.run_agent(
            [
                tool_call(
                    "edit_file",
                    {"path": "src/app.py", "old_string": 'return "bye"', "new_string": 'return "cya"'},
                    "c1",
                ),
                AIMessage(content="好的，我换个方案。"),
            ],
            approval,
        )
        self.assertIn('return "bye"', (self.root / "src" / "app.py").read_text(encoding="utf-8"))
        kinds = [event.kind for event in self.events]
        self.assertIn(E.DENIED, kinds)
        tool_messages = [m for m in result if isinstance(m, ToolMessage)]
        self.assertIn("用户拒绝", tool_messages[0].content)
        self.assertEqual(len(approval.requests), 1)

    # ---- 停止与步数上限 ----
    def test_cancel_prevents_any_model_call(self) -> None:
        model, result = self.run_agent([AIMessage(content="不应被调用")], cancel=True)
        self.assertEqual(model.calls, 0)
        self.assertEqual(self.events[0].kind, E.ERROR)
        self.assertIn("停止", self.events[0].text)
        self.assertEqual(len(result), 1)

    def test_max_steps_guard(self) -> None:
        loop_reply = tool_call("list_dir", {}, "c1")
        model, _ = self.run_agent([loop_reply], max_steps=3)
        self.assertEqual(model.calls, 3)
        last = self.events[-1]
        self.assertEqual(last.kind, E.ERROR)
        self.assertIn("最大步数", last.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
