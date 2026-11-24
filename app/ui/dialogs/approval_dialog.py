"""危险操作确认对话框。

默认按钮是「拒绝」：一次误触 Enter 就执行 rm -rf 的代价太大。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.agent import ApprovalDecision, ApprovalRequest
from app.ui.theme import mono_font

_DANGER_HINT = {
    "write": "此操作会修改工作区中的文件。请确认修改内容符合预期。",
    "exec": "此操作会执行 shell 命令，可能改动文件、安装依赖或访问网络。",
}


class ApprovalDialog(QDialog):
    """展示一次待确认的工具调用，返回允许或拒绝。"""

    def __init__(self, request: ApprovalRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._decision = ApprovalDecision(allowed=False, reason="用户关闭了确认窗口")

        self.setWindowTitle("需要你确认")
        self.setMinimumWidth(600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        hint = _DANGER_HINT.get(self._request.tool, "此操作需要你的确认。")

        title = QLabel("Agent 想执行一个操作")
        title.setProperty("role", "title")

        tool_label = QLabel(f"{self._request.tool}  ·  {self._request.title}")
        tool_label.setWordWrap(True)
        tool_label.setProperty("role", "accent")
        tool_label.setFont(mono_font(10))

        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setProperty("role", "danger")

        detail = QPlainTextEdit(self._request.detail)
        detail.setReadOnly(True)
        detail.setMaximumHeight(240)
        detail.setProperty("role", "code")
        detail.setFont(mono_font(9))

        self._remember = QCheckBox(f"本会话内不再询问 {self._request.tool} 的同类操作")

        reject_button = QPushButton("拒绝")
        reject_button.setDefault(True)
        reject_button.clicked.connect(self._on_reject)

        allow_button = QPushButton("允许执行")
        allow_button.setObjectName("danger")
        allow_button.clicked.connect(self._on_allow)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(reject_button)
        buttons.addWidget(allow_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(tool_label)
        layout.addWidget(hint_label)
        layout.addWidget(detail)
        layout.addWidget(self._remember)
        layout.addLayout(buttons)

        reject_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_allow(self) -> None:
        self._decision = ApprovalDecision(
            allowed=True,
            remember=self._remember.isChecked(),
            reason="用户允许",
        )
        self.accept()

    def _on_reject(self) -> None:
        self._decision = ApprovalDecision(allowed=False, reason="用户在确认框中拒绝")
        self.reject()

    def reject(self) -> None:
        if self._decision.allowed:
            self._decision = ApprovalDecision(allowed=False, reason="用户取消了操作")
        super().reject()

    def decision(self) -> ApprovalDecision:
        return self._decision
