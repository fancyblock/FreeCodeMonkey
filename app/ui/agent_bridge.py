"""把 agent 的跨线程需求接到 Qt 上。

runtime 在工作线程里调用 request() 并阻塞等待；本类把请求转发到界面线程弹窗，
拿到结论后唤醒工作线程。共享状态一律加锁，避免界面与工作线程互相踩。
"""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from app.agent import ApprovalDecision, ApprovalRequest
from app.ui import sound
from app.ui.dialogs.approval_dialog import ApprovalDialog

APPROVAL_TIMEOUT_SECONDS = 600


class AgentBridge(QObject):
    """agent 事件与审批请求的桥。每次任务创建一个实例。

    注意：信号不能叫 event。QObject.event() 是 Qt 的虚函数，用同名信号遮蔽它之后，
    同线程直连还能工作，但跨线程的队列投递会走 QObject::event()，
    结果是 PyQt 抛出 "native Qt signal is not callable" 并直接 abort 进程。
    """

    agent_event = pyqtSignal(object)
    approval_requested = pyqtSignal(object)

    def __init__(self, parent_window: Any = None) -> None:
        super().__init__(parent_window)
        self._parent_window = parent_window
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._remembered: set[str] = set()
        # 队列连接：emit 发生在工作线程，槽函数回到界面线程执行
        self.approval_requested.connect(self._on_approval_requested)

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
    @pyqtSlot(object)
    def _on_approval_requested(self, req: ApprovalRequest) -> None:
        # 任务在后台跑，用户可能没盯着屏幕；弹窗前先响一声
        sound.play_confirmation()
        dialog = ApprovalDialog(req, self._parent_window)
        dialog.exec()
        self._resolve(req.request_id, dialog.decision())

    def _resolve(self, request_id: str, decision: ApprovalDecision) -> None:
        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return
            signal_done, box = entry
            box["decision"] = decision
        signal_done.set()

    # ---- 供测试使用 ----
    def resolve_for_test(self, request_id: str, decision: ApprovalDecision) -> None:
        self._resolve(request_id, decision)

    # ---- 退出时清理 ----
    def abort_pending(self, reason: str = "程序正在退出") -> None:
        """把还没结论的审批全部判为拒绝，让工作线程能往下走。"""
        with self._lock:
            pending = list(self._pending.items())
        for request_id, _ in pending:
            self._resolve(request_id, ApprovalDecision(allowed=False, reason=reason))
