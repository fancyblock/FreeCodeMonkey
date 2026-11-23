"""ReAct 循环：模型产出工具调用 → 执行 → 结果回灌 → 继续推理。

刻意实现为线性循环：只有一个 agent，没有分支，也没有跨进程恢复的需求。
LangGraph 的 interrupt / checkpointer 主要买的是「持久化中断 + 图编排」，
这里用不上，反而会让中断语义穿透 QThread 变得更难调试。

将来若要做多 agent 或断点续跑，只需把 run() 换成图执行，
事件序列（events.py）与审批协议（approval.py）不需要改动。
"""

from __future__ import annotations

import json
import platform
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from . import events as E
from .approval import ApprovalChannel, ApprovalDecision, ApprovalRequest
from .events import AgentEvent
from .prompts import SYSTEM_PROMPT
from .tools import Workspace

MAX_STEPS = 25
HISTORY_LIMIT = 40
EVENT_PREVIEW_CHARS = 500


def content_text(content: Any) -> str:
    """把消息内容（字符串或多模态块列表）压平成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def clip_text(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def trim_history(messages: list[BaseMessage], limit: int = HISTORY_LIMIT) -> list[BaseMessage]:
    """截断历史，且不留下没有对应 AIMessage 的孤儿 ToolMessage。"""
    if len(messages) <= limit:
        return list(messages)
    trimmed = list(messages[-limit:])
    while trimmed and not isinstance(trimmed[0], (HumanMessage, AIMessage)):
        trimmed.pop(0)
    return trimmed


def _is_error(result: str) -> bool:
    return result.startswith(("错误：", "工具执行异常："))


def summarize_call(tool_name: str, args: dict[str, Any]) -> str:
    """一行描述一次工具调用，供审批弹窗与界面步骤共用。"""
    if tool_name == "run_command":
        return clip_text(str(args.get("command", "")), 90)
    if tool_name == "edit_file":
        path = str(args.get("path", ""))
        old = args.get("old_string")
        new = args.get("new_string")
        if old == "":
            return f"{path} · 新建文件"
        old_lines = len(str(old).splitlines())
        new_lines = len(str(new).splitlines())
        return f"{path} · {old_lines} 行 → {new_lines} 行"
    return clip_text(json.dumps(args, ensure_ascii=False), 90)


def _format_detail(args: dict[str, Any]) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(args)
    return clip_text(text, 4000)


class AgentRuntime:
    """一次 agent 任务的执行器。实例不可跨任务复用。"""

    def __init__(
        self,
        model: BaseChatModel,
        workspace: Workspace,
        approval: ApprovalChannel,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self._model = model
        self._workspace = workspace
        self._approval = approval
        self._max_steps = max_steps
        self._cancelled = False

    def cancel(self) -> None:
        """请求停止：只在两个步骤之间生效，不打断正在执行的命令。"""
        self._cancelled = True

    # ---- 主循环 ----
    def run(
        self,
        task: str,
        history: list[BaseMessage] | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> list[BaseMessage]:
        """执行任务，返回本轮完整消息序列（不含 system），供后续对话复用。"""
        emit = on_event or (lambda event: None)
        messages: list[BaseMessage] = [
            SystemMessage(
                content=SYSTEM_PROMPT.format(
                    root=self._workspace.root.as_posix(),
                    platform=f"{platform.system()} {platform.release()} · Python {platform.python_version()}",
                )
            ),
            *trim_history(list(history or [])),
            HumanMessage(content=task),
        ]
        bound = self._bind_tools()

        for step in range(1, self._max_steps + 1):
            if self._cancelled:
                emit(AgentEvent(E.ERROR, step=step, text="任务已被用户停止", ok=False))
                return messages[1:]

            emit(AgentEvent(E.STEP, step=step, text=f"第 {step} 步"))

            try:
                reply = bound.invoke(messages)
            except Exception as exc:  # noqa: BLE001 - 网络/鉴权/超时都归为一类
                raise RuntimeError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

            if not isinstance(reply, AIMessage):
                reply = AIMessage(content=str(reply))
            messages.append(reply)

            text = content_text(reply.content)
            tool_calls = list(getattr(reply, "tool_calls", None) or [])

            if not tool_calls:
                emit(AgentEvent(E.DONE, step=step, text=text or "（模型返回了空回复）"))
                return messages[1:]

            if text.strip():
                emit(AgentEvent(E.THOUGHT, step=step, text=text))

            for call in tool_calls:
                if self._cancelled:
                    emit(AgentEvent(E.ERROR, step=step, text="任务已被用户停止", ok=False))
                    return messages[1:]
                self._handle_call(call, step, messages, emit)

        emit(
            AgentEvent(
                E.ERROR,
                step=self._max_steps,
                text=f"已达到最大步数 {self._max_steps}，任务可能未完成。可以补充要求后重新发送。",
                ok=False,
            )
        )
        return messages[1:]

    # ---- 内部 ----
    def _bind_tools(self):
        try:
            return self._model.bind_tools(self._workspace.schemas())
        except NotImplementedError as exc:
            raise RuntimeError(
                "当前模型不支持工具调用（bind_tools 未实现）。"
                "请换成支持 function calling 的模型，例如 claude-sonnet、gpt-4o、deepseek-chat。"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - provider 校验失败也要能看懂
            raise RuntimeError(f"注册工具失败：{type(exc).__name__}: {exc}") from exc

    def _handle_call(
        self,
        call: dict[str, Any],
        step: int,
        messages: list[BaseMessage],
        emit: Callable[[AgentEvent], None],
    ) -> None:
        name = str(call.get("name") or "")
        args = call.get("args")
        if not isinstance(args, dict):
            args = {}
        call_id = str(call.get("id") or "")

        emit(AgentEvent(E.TOOL_CALL, step=step, tool=name, args=args))

        tool = self._workspace.get(name)
        if tool is None:
            available = "、".join(t.name for t in self._workspace.tools())
            result = f"错误：工具 {name} 不存在。可用工具：{available}"
        elif tool.requires_approval:
            decision = self._ask(name, args)
            if not decision.allowed:
                reason = decision.reason or "用户未说明原因"
                emit(AgentEvent(E.DENIED, step=step, tool=name, args=args, text=reason, ok=False))
                messages.append(
                    ToolMessage(
                        content=f"用户拒绝执行该操作（{reason}）。不要原样重试，先说明意图或换方案。",
                        tool_call_id=call_id,
                    )
                )
                return
            result = self._workspace.call(name, args)
        else:
            result = self._workspace.call(name, args)

        emit(
            AgentEvent(
                E.TOOL_RESULT,
                step=step,
                tool=name,
                text=clip_text(result, EVENT_PREVIEW_CHARS),
                ok=not _is_error(result),
            )
        )
        messages.append(ToolMessage(content=result, tool_call_id=call_id))

    def _ask(self, tool_name: str, args: dict[str, Any]) -> ApprovalDecision:
        request = ApprovalRequest(
            tool=tool_name,
            args=args,
            title=summarize_call(tool_name, args),
            detail=_format_detail(args),
        )
        try:
            return self._approval.request(request)
        except Exception as exc:  # noqa: BLE001 - 审批通道异常一律按拒绝处理
            return ApprovalDecision(allowed=False, reason=f"审批通道异常：{exc}")
