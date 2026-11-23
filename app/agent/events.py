"""运行时向界面广播的事件。

界面只消费事件、不感知循环实现，因此替换执行引擎（例如换成 LangGraph 图）
时只需要产出同样的事件序列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 事件类型
STEP = "step"                # 新一轮推理开始
THOUGHT = "thought"          # 模型的中间说明文字
TOOL_CALL = "tool_call"      # 即将执行某个工具
TOOL_RESULT = "tool_result"  # 工具执行完成
DENIED = "denied"            # 用户拒绝了某个工具调用
CONTEXT = "context"          # 本次请求的上下文占用
DONE = "done"                # 任务结束，附最终回答
ERROR = "error"              # 任务失败


@dataclass
class AgentEvent:
    """一个可展示的运行时事件。"""

    kind: str
    step: int = 0
    text: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    used_tokens: int = 0
    window_tokens: int = 0
