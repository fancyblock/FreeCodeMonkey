"""应用图标测试：资源齐全、能读出多尺寸、能挂到窗口上。

运行：.venv\\Scripts\\python.exe tests\\test_branding.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication

from app.ui.branding import app_icon, asset_path


class BrandingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_assets_exist(self) -> None:
        for name in ("app_icon.ico", "app_icon.png"):
            path = asset_path(name)
            self.assertTrue(path.is_file(), f"缺少图标资源：{path}")
            self.assertGreater(path.stat().st_size, 1024, name)

    def test_icon_is_not_null(self) -> None:
        icon = app_icon()
        self.assertFalse(icon.isNull())
        self.assertTrue(icon.availableSizes())

    def test_icon_has_windows_dpi_sizes(self) -> None:
        """任务栏在不同缩放比例下会挑不同尺寸，常用档位必须都在。"""
        sizes = {size.width() for size in app_icon().availableSizes()}
        for expected in (16, 24, 32, 48, 64, 256):
            self.assertIn(expected, sizes, f"缺少 {expected}px 图标")

    def test_icon_pixmap_renders(self) -> None:
        for px in (16, 32, 256):
            pixmap = app_icon().pixmap(px, px)
            self.assertFalse(pixmap.isNull(), f"{px}px 渲染失败")

    def test_application_window_icon_settable(self) -> None:
        self.app.setWindowIcon(app_icon())
        self.assertFalse(self.app.windowIcon().isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)
