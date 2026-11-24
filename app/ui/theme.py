"""集中样式层：语义色令牌、字体与全局 QSS。

颜色只在这里定义一次，控件和 HTML 片段都从 :func:`palette` 取值。要换主题时
只需要换一个 ``Palette`` 实例并重新 ``apply_theme``，不用逐个控件改样式。

约定：
- QSS 里不写 ``font-family``（Qt 对逗号回退链支持不稳），字体统一由
  :func:`ui_font` / :func:`mono_font` 以 ``QFont`` 形式下发。
- 全局只应用一份样式表，避免父控件的 ``setStyleSheet`` 向子控件隐式继承。
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QWidget

# 上下文占用告警阈值（百分比），README 里也按这两个数描述
CONTEXT_WARN_PERCENT = 60
CONTEXT_DANGER_PERCENT = 85


@dataclass(frozen=True)
class Palette:
    """一套完整的语义色。字段名表示用途，不表示具体颜色。"""

    name: str
    bg: str  # 窗口底色
    surface: str  # 面板 / 输入框 / 列表底色
    surface_alt: str  # 次级表面：代码块、悬停、用户消息底
    border: str
    border_strong: str
    text: str
    text_muted: str  # 面板标题、次要说明
    text_faint: str  # 占位符、禁用文字、系统提示
    accent: str  # 品牌色：用户消息、焦点边框
    accent_soft: str  # 选中行淡底
    accent_hover: str
    success: str  # 助手消息、连接成功
    warning: str  # 上下文 60%
    danger: str  # 上下文 85%、错误
    highlight: str  # 消息定位高亮底色
    on_accent: str  # 强调色上的文字


LIGHT = Palette(
    name="light",
    bg="#f6f7f9",
    surface="#ffffff",
    surface_alt="#f4f6f9",
    border="#e3e6ea",
    border_strong="#d3d8de",
    text="#1f2328",
    text_muted="#5b636d",
    text_faint="#8b939d",
    accent="#2f6feb",
    accent_soft="#e8f0fe",
    accent_hover="#2359c9",
    success="#1a7f4b",
    warning="#b26a00",
    danger="#c0392b",
    highlight="#fff4cc",
    on_accent="#ffffff",
)

_UI_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "Noto Sans CJK SC",
    "PingFang SC",
)
_MONO_FAMILIES = ("Consolas", "JetBrains Mono", "Cascadia Mono", "Courier New")

_active: Palette = LIGHT


def palette() -> Palette:
    """当前生效的色板。"""
    return _active


def set_palette(value: Palette) -> None:
    """切换色板（供后续的主题切换使用）；只改令牌，不重建界面。"""
    global _active
    _active = value
    # 图标颜色也跟着令牌走，延迟导入避免模块级循环依赖
    from app.ui import icons

    icons.clear_cache()


# ---- 字体 ----
def ui_font(point_size: int | None = None) -> QFont:
    """界面字体：中文优先用系统自带的黑体，缺字体时按顺序回退。"""
    font = QFont()
    font.setFamilies(list(_UI_FAMILIES))
    if point_size is not None:
        font.setPointSize(point_size)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def mono_font(point_size: int = 10) -> QFont:
    """等宽字体：代码、步骤输出、文件预览共用。"""
    font = QFont()
    font.setFamilies(list(_MONO_FAMILIES))
    font.setPointSize(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def context_level(percent: int) -> str:
    """上下文占用率对应的语义档位：ok / warn / danger。"""
    if percent >= CONTEXT_DANGER_PERCENT:
        return "danger"
    if percent >= CONTEXT_WARN_PERCENT:
        return "warn"
    return "ok"


def repolish(widget: QWidget) -> None:
    """动态属性变化后重算样式；Qt 不会自动重新匹配 QSS 选择器。"""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def set_state(widget: QWidget, state: str) -> None:
    """给控件打语义状态（success / warning / danger / 空串）并刷新样式。"""
    if widget.property("state") == state:
        return
    widget.setProperty("state", state)
    repolish(widget)


_QSS = Template(
    """
/* ---- 容器 ---- */
QMainWindow, QDialog { background: $bg; }
QWidget#root { background: $bg; }

/* ---- 文字 ---- */
QLabel { color: $text; }
QLabel:disabled { color: $text_faint; }
QLabel[role="section"] { color: $text_muted; font-weight: 600; padding: 2px 2px 6px 2px; }
QLabel[role="title"] { color: $text; font-size: 14px; font-weight: 600; }
QLabel[role="caption"] { color: $text_faint; font-size: 11px; }
QLabel[role="danger"] { color: $danger; font-size: 12px; }
QLabel[role="accent"] { color: $accent; }
QLabel[state="success"] { color: $success; }
QLabel[state="warning"] { color: $warning; }
QLabel[state="danger"] { color: $danger; }

/* ---- 按钮 ---- */
QPushButton {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 5px 14px;
}
QPushButton:hover { background: $surface_alt; border-color: $accent; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { background: $surface_alt; color: $text_faint; border-color: $border; }
QPushButton#primary {
    background: $accent;
    color: $on_accent;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:disabled {
    background: $border_strong;
    border-color: $border_strong;
    color: $surface;
}
QPushButton#danger { color: $danger; border-color: $danger; }
QPushButton#danger:hover { background: $danger; color: $on_accent; }
QPushButton#icon { background: transparent; border: none; padding: 2px; border-radius: 4px; }
QPushButton#icon:hover { background: $surface_alt; }
QPushButton#icon:pressed { background: $border; }

/* ---- 输入类 ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser, QTextEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit { padding: 5px 8px; }
QComboBox { padding: 4px 8px; padding-right: 22px; }
/* QTextBrowser 的内边距交给 documentMargin，避免双重留白 */
QPlainTextEdit, QTextEdit { padding: 4px; }
QTextBrowser { padding: 0; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus,
QTextBrowser:focus, QTextEdit:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {
    background: $surface_alt;
    color: $text_faint;
}
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QPlainTextEdit[role="code"] { background: $surface_alt; border: 1px solid $border; }

/* ---- 输入区里的审批卡 ---- */
QFrame#approvalPanel {
    background: $surface;
    border: 1px solid $danger;
    border-radius: 8px;
}
QFrame#approvalPanel QLabel { background: transparent; }

/* ---- 列表与树 ---- */
QTreeWidget, QListWidget {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 3px;
    outline: none;
}
QTreeWidget::item, QListWidget::item {
    color: $text;
    padding: 3px 4px;
    border-radius: 4px;
}
QTreeWidget::item:hover, QListWidget::item:hover { background: $surface_alt; }
QTreeWidget::item:selected, QListWidget::item:selected {
    background: $accent_soft;
    color: $text;
}
QHeaderView::section {
    background: $surface_alt;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border;
    padding: 4px 6px;
}

/* ---- 滚动条 ---- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $border_strong;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:horizontal {
    background: $border_strong;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: $text_faint;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ---- 菜单 / 状态栏 / 提示 ---- */
QMenuBar { background: $bg; color: $text; }
QMenuBar::item { background: transparent; padding: 4px 9px; border-radius: 4px; }
QMenuBar::item:selected { background: $surface; }
QMenu { background: $surface; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: $accent_soft; color: $text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }
QStatusBar { background: $bg; color: $text_muted; }
QStatusBar::item { border: none; }
QToolTip { background: $text; color: $surface; border: none; padding: 4px 7px; }

/* ---- 进度条 / 复选框 / 分栏 ---- */
QProgressBar {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 5px;
    font-size: 11px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk { background: $success; border-radius: 4px; }
QProgressBar[level="warn"]::chunk { background: $warning; }
QProgressBar[level="danger"]::chunk { background: $danger; }
QCheckBox { color: $text_muted; font-size: 12px; spacing: 6px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: $border_strong; }
"""
)


def build_qss(value: Palette | None = None) -> str:
    """把色板渲染成全局样式表。"""
    current = value or palette()
    return _QSS.substitute(
        bg=current.bg,
        surface=current.surface,
        surface_alt=current.surface_alt,
        border=current.border,
        border_strong=current.border_strong,
        text=current.text,
        text_muted=current.text_muted,
        text_faint=current.text_faint,
        accent=current.accent,
        accent_soft=current.accent_soft,
        accent_hover=current.accent_hover,
        success=current.success,
        warning=current.warning,
        danger=current.danger,
        highlight=current.highlight,
        on_accent=current.on_accent,
    )


def apply_theme(app: QApplication, value: Palette | None = None) -> None:
    """在 QApplication 级一次性应用主题：风格 + 字体 + 全局样式表。"""
    current = value or palette()
    set_palette(current)
    app.setStyle("Fusion")
    app.setFont(ui_font(10))
    app.setStyleSheet(build_qss(current))
