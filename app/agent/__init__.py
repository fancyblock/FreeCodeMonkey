"""编程智能体运行时。

与界面无关：只依赖事件回调与审批协议，可在无 GUI 环境下单独测试。
"""

from .approval import ApprovalChannel, ApprovalDecision, ApprovalRequest
from .events import AgentEvent
from .runtime import AgentRuntime
from .tools import Tool, ToolError, Workspace

__all__ = [
    "AgentEvent",
    "AgentRuntime",
    "ApprovalChannel",
    "ApprovalDecision",
    "ApprovalRequest",
    "Tool",
    "ToolError",
    "Workspace",
]
