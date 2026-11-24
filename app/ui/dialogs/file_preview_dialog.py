"""只读文件预览弹窗。

内容是一次性快照：打开时读一次，之后文件变了也不会自动跟随。
路径校验复用 agent 的 ``Workspace.resolve``，越界和不存在都当作错误文本显示，
不向界面抛异常。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.agent.tools import MAX_FILE_BYTES, ToolError, Workspace

MAX_PREVIEW_LINES = 2000

# UTF-8 BOM，读文件后丢掉，免得第一行多出一个看不见的字符
_BOM = chr(0xFEFF)


class FilePreviewDialog(QDialog):
    """展示工作区内某个文件的内容，带行号，只读。"""

    def __init__(self, root: Path, rel_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"预览 · {rel_path}")
        self.resize(760, 640)

        self._caption = QLabel()
        self._caption.setStyleSheet("color:#5f6368;font-size:11px;")
        self._caption.setWordWrap(True)

        self._body = QPlainTextEdit()
        self._body.setReadOnly(True)
        self._body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # 行号靠 tab 对齐；默认制表位太宽，收窄到四个字符
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._body.setFont(mono)
        self._body.setTabStopDistance(QFontMetrics(mono).horizontalAdvance(" ") * 4)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._caption)
        layout.addWidget(self._body, 1)
        layout.addWidget(buttons)

        self._body.setPlainText(read_preview(Path(root), rel_path, self._caption))


def read_preview(root: Path, rel_path: str, caption: QLabel | None = None) -> str:
    """按预览规则读取文本并编号；失败时返回可直接显示的说明文字。

    与 ``read_file`` 共用同一套路径约束和行号格式，用户在这里看到的内容
    和模型读到的是同一种文本。
    """

    def set_caption(text: str) -> None:
        if caption is not None:
            caption.setText(text)

    try:
        path = Workspace(root, read_only=True).resolve(rel_path)
    except (ValueError, ToolError) as exc:
        set_caption("无法打开该文件")
        return str(exc)

    if path.is_dir():
        set_caption("无法打开该文件")
        return f"{rel_path} 是目录，无法按文件预览。"

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES)
            clipped_bytes = bool(handle.read(1))
    except OSError as exc:
        set_caption("无法打开该文件")
        return f"读取失败：{exc}"

    binary_message = "该文件不是 UTF-8 文本（可能是二进制），无法预览。"
    if b"\x00" in raw:
        set_caption(f"{rel_path} · {_format_size(size)}")
        return binary_message

    try:
        text = raw.decode("utf-8", errors="replace" if clipped_bytes else "strict")
    except UnicodeDecodeError:
        set_caption(f"{rel_path} · {_format_size(size)}")
        return binary_message

    text = text.lstrip(_BOM)
    lines = text.splitlines()
    total = len(lines)
    shown = lines[:MAX_PREVIEW_LINES]

    if caption is not None:
        parts = [rel_path, _format_size(size), f"共 {total} 行", "只读快照"]
        if clipped_bytes:
            parts.append(f"文件大于 {_format_size(MAX_FILE_BYTES)}，只读取了开头部分")
        if total > MAX_PREVIEW_LINES:
            parts.append(f"仅显示前 {MAX_PREVIEW_LINES} 行")
        caption.setText(" · ".join(parts))

    if total == 0:
        return "（空文件）"
    return "\n".join(
        f"{number:>5}\t{line}" for number, line in enumerate(shown, start=1)
    )


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"
