"""输入区：确认卡 + Agent 模式下拉 + 多行文本输入 + 发送 / 停止按钮。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.agent import ApprovalRequest
from app.constants import DEFAULT_CONTEXT_WINDOW
from app.ui.icons import icon
from app.ui.theme import context_level, palette, repolish
from app.ui.widgets.approval_panel import ApprovalPanel

_MODE_OPTIONS = (
    ("plan", "Plan", "只读：分析代码并给出修改计划，不改文件也不执行命令"),
    ("build", "Build", "可写：直接修改代码并执行命令，危险操作会弹窗确认"),
)
_DEFAULT_MODE = "build"


class _PromptEdit(QPlainTextEdit):
    """支持 Ctrl+Enter 提交的文本编辑器。"""

    submit_shortcut = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit_shortcut.emit()
            return
        super().keyPressEvent(event)


class PromptInput(QWidget):
    """提示词输入组件。"""

    submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)
    new_session_requested = pyqtSignal()
    approval_resolved = pyqtSignal(str, object)  # request_id, ApprovalDecision
    approval_size_changed = pyqtSignal()  # 卡片展开 / 收起参数后的高度变化

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._approval = ApprovalPanel()
        self._edit = _PromptEdit()
        self._mode_combo = QComboBox()
        self._context_bar = QProgressBar()
        self._used_tokens = 0
        self._window_tokens = DEFAULT_CONTEXT_WINDOW
        self._send_button = QPushButton("发送 (Ctrl+Enter)")
        self._send_button.setObjectName("primary")
        self._send_button.setIcon(icon("send", palette().on_accent, 14))
        self._stop_button = QPushButton("停止")
        self._stop_button.setIcon(icon("stop", size=14))
        self._stop_button.setVisible(False)
        self._new_session_button = QPushButton("新建会话")
        self._new_session_button.setIcon(icon("plus", size=14))
        self._new_session_button.setToolTip("开一段新的对话，当前会话会保留在左侧列表里")
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        self._edit.setPlaceholderText("描述你想让 agent 完成的编程任务…")
        self._edit.setMinimumHeight(80)

        self._mode_combo.setToolTip("切换 agent 的工作模式")
        for mode, label, tip in _MODE_OPTIONS:
            self._mode_combo.addItem(label, mode)
            self._mode_combo.setItemData(
                self._mode_combo.count() - 1,
                f"{label}：{tip}",
                Qt.ItemDataRole.ToolTipRole,
            )
        default_index = self._mode_combo.findData(_DEFAULT_MODE)
        if default_index >= 0:
            self._mode_combo.setCurrentIndex(default_index)

        self._context_bar.setRange(0, 100)
        self._context_bar.setValue(0)
        self._context_bar.setFormat("%p%")
        self._context_bar.setTextVisible(True)
        self._context_bar.setFixedWidth(130)
        self._context_bar.setMaximumHeight(18)
        self._context_bar.setToolTip("上下文占用：尚无请求")

        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Agent："))
        buttons.addWidget(self._mode_combo)
        buttons.addWidget(self._new_session_button)
        buttons.addStretch(1)
        buttons.addWidget(self._context_bar)
        buttons.addWidget(self._stop_button)
        buttons.addWidget(self._send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # 确认卡贴在输入框正上方，出现时顶起输入区而不是盖住对话
        layout.addWidget(self._approval)
        layout.addWidget(self._edit)
        layout.addLayout(buttons)

    def _connect_signals(self) -> None:
        self._send_button.clicked.connect(self._emit_text)
        self._new_session_button.clicked.connect(self.new_session_requested.emit)
        self._stop_button.clicked.connect(self.stop_requested.emit)
        self._edit.submit_shortcut.connect(self._emit_text)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._approval.resolved.connect(self.approval_resolved.emit)
        self._approval.height_changed.connect(self._on_approval_size_changed)

    def _on_approval_size_changed(self) -> None:
        self.updateGeometry()
        self.approval_size_changed.emit()

    def _on_mode_changed(self, index: int) -> None:
        mode = self._mode_combo.itemData(index)
        if mode:
            self.mode_changed.emit(str(mode))

    def _emit_text(self) -> None:
        text = self.to_plain_text()
        if text:
            self.submitted.emit(text)

    def to_plain_text(self) -> str:
        return self._edit.toPlainText().strip()

    def mode(self) -> str:
        """当前选中的 agent 模式：plan 或 build。"""
        data = self._mode_combo.currentData()
        return str(data) if data else _DEFAULT_MODE

    # ---- 上下文占用 ----
    def set_context_usage(self, used_tokens: int, window_tokens: int) -> None:
        """更新进度条：已用 token 占模型上下文窗口的比例。"""
        self._used_tokens = max(0, int(used_tokens))
        self._window_tokens = max(1, int(window_tokens))
        percent = min(100, round(self._used_tokens * 100 / self._window_tokens))
        self._context_bar.setValue(percent)
        self._context_bar.setToolTip(
            f"上下文占用：约 {self._used_tokens:,} / {self._window_tokens:,} tokens"
            f"（{percent}%）"
        )
        # 只切语义档位，颜色由全局 QSS 的 [level=...] 规则决定
        level = context_level(percent)
        if self._context_bar.property("level") != level:
            self._context_bar.setProperty("level", level)
            repolish(self._context_bar)

    def set_context_window(self, window_tokens: int) -> None:
        """模型换窗口时（例如切 provider）按新窗口重算比例。"""
        self.set_context_usage(self._used_tokens, window_tokens)

    def reset_context(self) -> None:
        self._used_tokens = 0
        self.set_context_usage(0, self._window_tokens)

    def clear(self) -> None:
        self._edit.clear()

    # ---- 审批卡 ----
    def show_approval(self, request: ApprovalRequest) -> None:
        """把一次待确认的工具调用放进输入区。"""
        self._approval.set_request(request)

    def clear_approval(self, request_id: str | None = None) -> None:
        """收起审批卡；给了 request_id 时只收对应的那张。"""
        self._approval.clear(request_id)

    def pending_request_id(self) -> str:
        return self._approval.pending_request_id()

    def has_pending_approval(self) -> bool:
        return self._approval.has_pending()

    def approval_height(self) -> int:
        """确认卡要多占的高度（含与输入框之间的间距）。"""
        return self._approval.sizeHint().height() + 6

    def set_running(self, running: bool) -> None:
        """任务进行中：禁用输入与发送，显示停止按钮。"""
        self._edit.setDisabled(running)
        self._send_button.setDisabled(running)
        self._mode_combo.setDisabled(running)
        # 任务执行中不允许换会话，按钮同步置灰，避免点了没反应
        self._new_session_button.setDisabled(running)
        self._stop_button.setVisible(running)
        if not running:
            self._edit.setFocus()
