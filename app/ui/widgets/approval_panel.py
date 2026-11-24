"""输入区里的审批卡片：就地确认一次工具调用。

以前用模态对话框：它会把整个窗口锁住，而任务本来就跑在后台线程里，界面
没必要停下来。卡片贴在输入框上方，出现时只代表「等你决定」，不点「允许执行」
就不会放行；默认焦点落在「拒绝」上，误按 Enter 也不会执行危险操作。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.agent import ApprovalDecision, ApprovalRequest
from app.ui.theme import mono_font

# 工具名对应一句话说明；tool 用运行时登记的名字，不是动作类别
_DANGER_HINT = {
    "edit_file": "此操作会修改工作区中的文件。请确认修改内容符合预期。",
    "run_command": "此操作会执行 shell 命令，可能改动文件、安装依赖或访问网络。",
}
_DEFAULT_HINT = "此操作需要你的确认。"


class ApprovalPanel(QFrame):
    """展示一次待确认的工具调用，把用户的选择发出去。"""

    resolved = pyqtSignal(str, object)  # request_id, ApprovalDecision
    height_changed = pyqtSignal()  # 展开 / 收起参数后卡片高度变了

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("approvalPanel")
        self._request: ApprovalRequest | None = None
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        title = QLabel("需要你确认")
        title.setProperty("role", "title")

        self._tool_label = QLabel()
        self._tool_label.setProperty("role", "accent")
        self._tool_label.setWordWrap(True)
        self._tool_label.setFont(mono_font(10))

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(title)
        header.addWidget(self._tool_label, 1)

        self._hint_label = QLabel()
        self._hint_label.setProperty("role", "danger")
        self._hint_label.setWordWrap(True)

        self._detail_toggle = QPushButton("查看参数")
        self._detail_toggle.setCheckable(True)
        self._detail_toggle.setToolTip("展开这次调用的完整参数")
        self._detail_toggle.toggled.connect(self._on_toggle_detail)

        self._remember = QCheckBox()
        self._remember.setToolTip("勾选后本次任务内同类操作直接放行")

        self._reject_button = QPushButton("拒绝")
        self._allow_button = QPushButton("允许执行")
        self._allow_button.setObjectName("danger")
        self._reject_button.clicked.connect(self._on_reject)
        self._allow_button.clicked.connect(self._on_allow)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self._detail_toggle)
        footer.addSpacing(6)
        footer.addWidget(self._remember)
        footer.addStretch(1)
        footer.addWidget(self._reject_button)
        footer.addWidget(self._allow_button)

        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setProperty("role", "code")
        self._detail.setFont(mono_font(9))
        self._detail.setMinimumHeight(80)
        self._detail.setMaximumHeight(150)
        self._detail.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self._hint_label)
        layout.addWidget(self._detail)
        layout.addLayout(footer)

    # ---- 对外 ----
    def set_request(self, request: ApprovalRequest) -> None:
        """铺开一次新请求；之前那张卡上的勾选与展开状态不带到下一次。"""
        self._request = request
        self._tool_label.setText(f"{request.tool} · {request.title}")
        self._hint_label.setText(_DANGER_HINT.get(request.tool, _DEFAULT_HINT))
        self._detail.setPlainText(request.detail)
        self._detail_toggle.setChecked(False)
        self._detail.hide()
        self._remember.setChecked(False)
        self._remember.setText(f"本任务内不再询问 {request.tool} 的同类操作")
        self.show()
        # 安全默认：焦点在「拒绝」上，误按 Enter 不会放行
        self._reject_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def clear(self, request_id: str | None = None) -> None:
        """收起卡片；给了 request_id 时只在它仍是当前请求时才收。"""
        if request_id is not None and self.pending_request_id() != request_id:
            return
        self._request = None
        self._detail.clear()
        self.hide()

    def pending_request_id(self) -> str:
        """还等着裁决的请求 id；没有则为空串。"""
        if self._request is None:
            return ""
        return self._request.request_id

    def has_pending(self) -> bool:
        return self._request is not None

    # ---- 内部 ----
    def _on_toggle_detail(self, checked: bool) -> None:
        self._detail.setVisible(checked)
        self._detail_toggle.setText("收起参数" if checked else "查看参数")
        # 参数区很占地方：先让布局按新高度重算，再请主窗口给输入区让出位置
        self.updateGeometry()
        self.height_changed.emit()

    def _on_allow(self) -> None:
        request = self._request
        if request is None:
            return
        self._finish(
            request,
            ApprovalDecision(
                allowed=True,
                remember=self._remember.isChecked(),
                reason="用户在输入区允许",
            ),
        )

    def _on_reject(self) -> None:
        request = self._request
        if request is None:
            return
        self._finish(request, ApprovalDecision(allowed=False, reason="用户在输入区拒绝"))

    def _finish(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        # 先收起卡片再发信号：槽函数里可能马上推进到下一步
        self.clear()
        self.resolved.emit(request.request_id, decision)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.has_pending():
            self._on_reject()
            return
        super().keyPressEvent(event)
