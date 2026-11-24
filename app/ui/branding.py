"""应用程序图标。

``assets/app_icon.ico`` 是从仓库根目录的 ``icon.png`` 处理来的：去掉右下角
的水印、按原圆角半径恢复轮廓，再导出 16/20/24/32/40/48/64/128/256 九种尺寸。
尺寸齐全，所以直接交给 ``QIcon`` 由 Qt 按当前 DPI 自行挑选，运行时不做缩放。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtGui import QIcon

_ASSETS = Path(__file__).resolve().parent / "assets"


def asset_path(name: str) -> Path:
    """资源文件的绝对路径，供打包脚本与测试引用。"""
    return _ASSETS / name


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """窗口与任务栏图标；ICO 缺失时退回同目录的 PNG。"""
    ico = asset_path("app_icon.ico")
    if ico.is_file():
        candidate = QIcon(str(ico))
        if not candidate.isNull():
            return candidate
    return QIcon(str(asset_path("app_icon.png")))
