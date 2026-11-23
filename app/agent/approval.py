"""危险操作的审批约定。

AgentRuntime 只依赖 ApprovalChannel 这一个方法：阻塞等待用户裁决。
界面层用 Qt 信号实现，测试里用假通道直接返回结果，两边共用同一套语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class ApprovalRequest:
    """一次待裁决的工具调用。"""

    tool: str
    args: dict[str, Any]
    title: str          # 单行摘要，弹窗标题用
    detail: str         # 完整参数预览
    request_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class ApprovalDecision:
    allowed: bool
    remember: bool = False       # 本会话内同类工具不再询问
    reason: str = ""


class ApprovalChannel(Protocol):
    """审批通道协议。实现方必须阻塞直到有结论。"""

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        ...


class AutoApprove:
    """无条件放行，供测试与非交互场景使用。"""

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(allowed=True, reason="auto")


class AutoDeny:
    """无条件拒绝，供测试使用。"""

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(allowed=False, reason="denied by test channel")
