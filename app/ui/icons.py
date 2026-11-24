"""内联 SVG 图标。

图标以字符串形式随代码走，不引入二进制资源文件，也不依赖 .qrc。
路径数据取自 Lucide（https://lucide.dev，ISC 协议），按 24×24 视口绘制，
渲染时把 ``currentColor`` 换成当前主题的令牌色，因此能跟主题一起变。

``PyQt6.QtSvg`` 不可用时退回 Qt 自带的标准图标，保证界面不会因为缺一个
模块而整体起不来。
"""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle

from app.ui.theme import palette

try:  # pragma: no cover - 取决于安装的 PyQt6 组件
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover
    QSvgRenderer = None  # type: ignore[assignment]

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
    ' stroke="{color}" stroke-width="2" stroke-linecap="round"'
    ' stroke-linejoin="round">{body}</svg>'
)

_BODIES = {
    # 循环箭头
    "refresh": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>',
    # 纸飞机
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    # 实心方块 = 停止
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    # 文件夹
    "folder": (
        '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9'
        'l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>'
    ),
    # 加号 = 新建
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    # 横向滑杆 = 设置
    "settings": (
        '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/>'
        '<line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/>'
        '<line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/>'
        '<line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/>'
        '<line x1="16" x2="16" y1="18" y2="22"/>'
    ),
}

_FALLBACKS = {
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "send": QStyle.StandardPixmap.SP_ArrowUp,
    "stop": QStyle.StandardPixmap.SP_MediaStop,
    "folder": QStyle.StandardPixmap.SP_DirIcon,
    "plus": QStyle.StandardPixmap.SP_FileDialogNewFolder,
    "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
}


def _device_pixel_ratio() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return float(ratio) if ratio else 1.0


def _fallback_icon(name: str) -> QIcon:
    app = QApplication.instance()
    pixmap = _FALLBACKS.get(name)
    if app is None or pixmap is None:
        return QIcon()
    return app.style().standardIcon(pixmap)


@lru_cache(maxsize=64)
def icon(name: str, color: str = "", size: int = 16) -> QIcon:
    """取一个图标；``color`` 留空时跟随当前主题的次要文字色。"""
    body = _BODIES.get(name)
    if body is None or QSvgRenderer is None:
        return _fallback_icon(name)

    tint = color or palette().text_muted
    ratio = _device_pixel_ratio()
    pixel_size = max(1, round(size * ratio))
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    renderer = QSvgRenderer(
        QByteArray(_SVG_TEMPLATE.format(color=tint, body=body).encode("utf-8"))
    )
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def clear_cache() -> None:
    """主题换色后调用，让下次取图标时用新的令牌色重新渲染。"""
    icon.cache_clear()
