"""把 agent 的跨线程需求接到 Qt 上。

runtime 在工作线程里调用 request() 并阻塞等待；本类把请求转发到界面线程（主窗口
在输入区里铺一张确认卡），拿到结论后唤醒工作线程。共享状态一律加锁，避免界面与
工作线程互相踩。
"""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from app.agent import ApprovalDecision, ApprovalRequest

APPROVAL_TIMEOUT_SECONDS = 600


class AgentBridge(QObject):
    """agent 事件与审批请求的桥。每次任务创建一个实例。

    注意：信号不能叫 event。QObject.event() 是 Qt 的虚函数，用同名信号遮蔽它之后，
    同线程直连还能工作，但跨线程的队列投递会走 QObject::event()，
    结果是 PyQt 抛出 "native Qt signal is not callable" 并直接 abort 进程。
    """

    agent_event = pyqtSignal(object)
    approval_requested = pyqtSignal(object)
    # request_id, 失效原因；界面据此收起已经没意义的确认卡
    approval_expired = pyqtSignal(str, str)

    def __init__(self, parent_window: Any = None) -> None:
        super().__init__(parent_window)
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._remembered: set[str] = set()

    # ---- 工作线程侧 ----
    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        """阻塞直到用户在界面上做出选择。超时按拒绝处理。"""
        with self._lock:
            if req.tool in self._remembered:
                return ApprovalDecision(allowed=True, reason="本会话已允许该类操作")
            signal_done = threading.Event()
            box: dict[str, Any] = {}
            self._pending[req.request_id] = (signal_done, box)

        self.approval_requested.emit(req)
        finished = signal_done.wait(timeout=APPROVAL_TIMEOUT_SECONDS)

        with self._lock:
            entry = self._pending.pop(req.request_id, None)
        if not finished:
            # 卡片一直没人点：告诉界面把它收起来，免得停在一个早已失效的请求上
            self.approval_expired.emit(req.request_id, "等待确认超时")
            return ApprovalDecision(allowed=False, reason="等待确认超时")
        if entry is None:
            return ApprovalDecision(allowed=False, reason="确认请求已失效")

        decision = box.get("decision")
        if not isinstance(decision, ApprovalDecision):
            return ApprovalDecision(allowed=False, reason="未收到确认结果")
        if decision.allowed and decision.remember:
            with self._lock:
                self._remembered.add(req.tool)
        return decision

    # ---- 界面线程侧 ----
    def resolve(self, request_id: str, decision: ApprovalDecision) -> None:
        """界面给出结论：唤醒等待中的工作线程。重复结论会被忽略。"""
        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return
            signal_done, box = entry
            box["decision"] = decision
        signal_done.set()

    # ---- 退出时清理 ----
    def abort_pending(self, reason: str = "程序正在退出") -> None:
        """把还没结论的审批全部判为拒绝，让工作线程能往下走。"""
        with self._lock:
            pending = list(self._pending.items())
        for request_id, _ in pending:
            self.resolve(request_id, ApprovalDecision(allowed=False, reason=reason))
            self.approval_expired.emit(request_id, reason)
