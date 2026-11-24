"""样式层测试：令牌、全局 QSS、字体、图标与语义档位。

运行：.venv\\Scripts\\python.exe tests\\test_theme.py
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.ui.icons import clear_cache, icon
from app.ui.theme import (
    CONTEXT_DANGER_PERCENT,
    CONTEXT_WARN_PERCENT,
    LIGHT,
    Palette,
    apply_theme,
    build_qss,
    context_level,
    mono_font,
    palette,
    set_palette,
    ui_font,
)
from app.ui.widgets import ChatView, PromptInput

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"


class ThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._style = cls.app.style().objectName()
        cls._sheet = cls.app.styleSheet()
        cls._font = cls.app.font()

    @classmethod
    def tearDownClass(cls) -> None:
        # 全局样式是应用级状态，别让它漏给同一进程里的其他测试
        cls.app.setStyleSheet(cls._sheet)
        cls.app.setFont(cls._font)
        set_palette(LIGHT)
        clear_cache()

    def test_palette_tokens_are_hex(self) -> None:
        for field in fields(Palette):
            if field.name == "name":
                continue
            value = getattr(LIGHT, field.name)
            self.assertRegex(value, r"^#[0-9a-f]{6}$", f"{field.name} = {value}")

    def test_qss_covers_core_widgets(self) -> None:
        qss = build_qss(LIGHT)
        for selector in (
            "QPushButton#primary",
            "QPushButton#danger",
            "QTextBrowser",
            "QComboBox QAbstractItemView",
            'QProgressBar[level="danger"]::chunk',
            "QScrollBar::handle:vertical",
            "QToolTip",
        ):
            self.assertIn(selector, qss)

    def test_qss_avoids_unsupported_properties(self) -> None:
        qss = build_qss(LIGHT)
        # Qt 样式表不支持这几个属性，写了只会安静地失效
        for prop in ("box-shadow", "transition:", "transform:", "line-height"):
            self.assertNotIn(prop, qss)

    def test_apply_theme_installs_fusion_and_tokens(self) -> None:
        apply_theme(self.app, LIGHT)
        # 设了全局样式表后 style() 变成 QStyleSheetStyle 代理，底层风格名
        # 读不到，所以这里校验的是真正生效的样式表与令牌
        self.assertIn(LIGHT.accent, self.app.styleSheet())
        self.assertIn(LIGHT.danger, self.app.styleSheet())
        self.assertEqual(palette(), LIGHT)
        self.assertEqual(self.app.styleSheet(), build_qss(LIGHT))

    def test_fonts_prefer_cjk_and_monospace(self) -> None:
        ui = ui_font(10)
        mono = mono_font(9)
        self.assertEqual(ui.families()[0], "Microsoft YaHei UI")
        self.assertEqual(mono.families()[0], "Consolas")
        self.assertEqual(mono.pointSize(), 9)

    def test_icon_renders_every_name(self) -> None:
        for name in ("refresh", "send", "stop", "folder", "settings"):
            result = icon(name, size=16)
            self.assertIsInstance(result, QIcon)
            self.assertFalse(result.isNull(), name)
            self.assertTrue(result.availableSizes(), name)

    def test_icon_color_is_applied(self) -> None:
        red = icon("send", "#ff0000", 16).pixmap(16, 16).toImage()
        green = icon("send", "#00ff00", 16).pixmap(16, 16).toImage()
        self.assertNotEqual(red, green)

    def test_context_level_thresholds(self) -> None:
        self.assertEqual(context_level(0), "ok")
        self.assertEqual(context_level(CONTEXT_WARN_PERCENT - 1), "ok")
        self.assertEqual(context_level(CONTEXT_WARN_PERCENT), "warn")
        self.assertEqual(context_level(CONTEXT_DANGER_PERCENT - 1), "warn")
        self.assertEqual(context_level(CONTEXT_DANGER_PERCENT), "danger")
        self.assertEqual(context_level(100), "danger")

    def test_progress_bar_switches_level_property(self) -> None:
        widget = PromptInput()
        widget.set_context_usage(50_000, 128_000)
        self.assertEqual(widget._context_bar.property("level"), "ok")
        widget.set_context_usage(90_000, 128_000)
        self.assertEqual(widget._context_bar.property("level"), "warn")
        widget.set_context_usage(120_000, 128_000)
        self.assertEqual(widget._context_bar.property("level"), "danger")

    def test_chat_view_uses_palette_tokens(self) -> None:
        view = ChatView()
        view.append_message("user", "hello")
        view.append_step("读文件", "content", tone="ok")
        html = view.toHtml()
        self.assertIn(LIGHT.surface_alt, html)
        self.assertIn(LIGHT.success, html)

    def test_no_widget_level_stylesheets(self) -> None:
        """控件级 setStyleSheet 会向整个子树继承，样式统一由 theme 提供。"""
        offenders = []
        for path in _APP_ROOT.rglob("*.py"):
            if path.name == "theme.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"\.setStyleSheet\(", text):
                offenders.append(str(path.relative_to(_APP_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
