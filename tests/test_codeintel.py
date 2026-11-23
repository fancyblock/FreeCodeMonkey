"""离线验证 tree-sitter 语法工具：outline / read_symbol / syntax_check。

不依赖模型与网络，全部基于临时目录里的真实文件。
运行：.venv\\Scripts\\python.exe tests\\test_codeintel.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import Workspace
from app.agent.syntax import (
    MAX_OUTLINE_FILES,
    Symbol,
    available,
    check_syntax,
    collect_symbols,
    outline,
    read_symbol,
)

SAMPLE = '''"""模块文档。"""

VALUE = 1


@decorator
class Base:
    """基类。"""

    def method(self, a, b):
        def inner():
            return a
        return inner


async def fetch(url: str) -> str:
    """拉取。"""
    return url
'''

DUPLICATES = '''class A:
    def run(self):
        return "a-value"


class B:
    def run(self):
        return "b-value"
'''

CRLF_SAMPLE = "def alpha():\r\n    # 中文注释\r\n    return 1\r\n"

BROKEN = "def broken(:\n    pass\n"


@unittest.skipUnless(available(), "未安装 tree-sitter / tree-sitter-python")
class SyntaxToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sample = self.root / "pkg" / "sample.py"
        self.sample.parent.mkdir()
        self.sample.write_text(SAMPLE, encoding="utf-8")
        (self.root / "pkg" / "dupes.py").write_text(DUPLICATES, encoding="utf-8")
        (self.root / "crlf.py").write_bytes(CRLF_SAMPLE.encode("utf-8"))
        (self.root / "broken.py").write_text(BROKEN, encoding="utf-8")
        (self.root / "notes.txt").write_text("TODO\n", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "hidden.py").write_text("def hidden():\n    pass\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "dep.py").write_text(
            "def dep():\n    pass\n", encoding="utf-8"
        )
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def call(self, name: str, args: dict) -> str:
        return self.workspace.call(name, args)

    # ---- outline ----
    def test_outline_file_depth_one(self) -> None:
        text = outline(self.workspace, "pkg/sample.py")
        self.assertIn("class Base", text)
        self.assertIn("def fetch", text)
        self.assertNotIn("def method", text)
        self.assertNotIn("错误：", text)
        # 装饰器算在类定义的范围内：起始行是 @decorator 那一行
        raw, tree = self._parse("pkg/sample.py")
        base = next(
            symbol for symbol in collect_symbols(raw, tree, "pkg/sample.py") if symbol.name == "Base"
        )
        self.assertEqual((base.start_line, base.end_line), (6, 13))

    def test_outline_depth_two_includes_members(self) -> None:
        text = outline(self.workspace, "pkg/sample.py", depth=2)
        self.assertIn("方法 def method", text)
        self.assertNotIn("def inner", text)

    def test_outline_depth_three_includes_nested(self) -> None:
        text = outline(self.workspace, "pkg/sample.py", depth=3)
        self.assertIn("嵌套函数 def inner", text)

    def test_outline_directory_skips_ignored_dirs(self) -> None:
        text = outline(self.workspace, ".", depth=1)
        self.assertIn("pkg/sample.py", text)
        self.assertNotIn("hidden.py", text)
        self.assertNotIn("node_modules", text)

    def test_outline_name_filter(self) -> None:
        text = outline(self.workspace, "pkg", name_filter="fetch")
        self.assertIn("def fetch", text)
        self.assertNotIn("class Base", text)

    def test_outline_reports_bad_regex(self) -> None:
        text = self.call("outline", {"path": "pkg", "name_filter": "["})
        self.assertTrue(text.startswith("错误："))
        self.assertIn("正则无效", text)

    def test_outline_caps_file_count(self) -> None:
        many = self.root / "many"
        many.mkdir()
        total = MAX_OUTLINE_FILES + 5
        for index in range(total):
            (many / f"m{index:03d}.py").write_text(
                f"def f{index}():\n    pass\n", encoding="utf-8"
            )
        text = outline(self.workspace, "many", depth=1)
        self.assertIn("已跳过", text)

    def test_outline_unsupported_extension(self) -> None:
        text = self.call("outline", {"path": "notes.txt"})
        self.assertTrue(text.startswith("错误："))
        self.assertIn("不支持的文件类型", text)

    def test_outline_escapes_workspace(self) -> None:
        text = self.call("outline", {"path": "../outside"})
        self.assertTrue(text.startswith("错误："))
        self.assertIn("超出工作区范围", text)

    # ---- read_symbol ----
    def test_read_symbol_dotted_name(self) -> None:
        text = read_symbol(self.workspace, "pkg/sample.py", "Base.method")
        self.assertIn("def method", text)
        self.assertIn("def inner", text)
        self.assertIn("return inner", text)
        self.assertIn("Base.method", text)

    def test_read_symbol_duplicates_need_line(self) -> None:
        text = read_symbol(self.workspace, "pkg/dupes.py", "run")
        self.assertIn("2 处定义", text)
        self.assertIn("A.run", text)
        self.assertIn("B.run", text)

    def test_read_symbol_disambiguates_by_line(self) -> None:
        text = read_symbol(self.workspace, "pkg/dupes.py", "run", line=7)
        self.assertIn("b-value", text)
        self.assertNotIn("a-value", text)

    def test_read_symbol_suffix_match(self) -> None:
        text = read_symbol(self.workspace, "pkg/dupes.py", "B.run")
        self.assertIn("b-value", text)
        self.assertNotIn("a-value", text)

    def test_read_symbol_not_found(self) -> None:
        text = self.call("read_symbol", {"path": "pkg/sample.py", "name": "nope"})
        self.assertTrue(text.startswith("错误："))
        self.assertIn("未找到符号", text)

    def test_read_symbol_on_directory_is_rejected(self) -> None:
        text = self.call("read_symbol", {"path": "pkg", "name": "Base"})
        self.assertTrue(text.startswith("错误："))
        self.assertIn("是目录", text)

    def test_read_symbol_line_numbers_match_read_file_for_crlf(self) -> None:
        symbol = read_symbol(self.workspace, "crlf.py", "alpha")
        read_file = self.call("read_file", {"path": "crlf.py", "start_line": 1, "end_line": 3})
        first_symbol_line = symbol.splitlines()[1]
        self.assertIn(first_symbol_line, read_file)
        self.assertIn("def alpha():", first_symbol_line)

    # ---- syntax_check ----
    def test_syntax_check_broken_file(self) -> None:
        text = check_syntax(self.workspace, "broken.py")
        self.assertIn("语法检查", text)
        # `def broken(:` 缺一个右括号，tree-sitter 报 MISSING 节点
        self.assertIn("MISSING", text)
        self.assertIn("1:", text)
        self.assertIn("def broken(:", text)

    def test_syntax_check_clean_file(self) -> None:
        text = check_syntax(self.workspace, "pkg/sample.py")
        self.assertTrue(text.startswith("语法通过"))

    def test_syntax_check_directory_only_reports_bad_files(self) -> None:
        text = check_syntax(self.workspace, ".")
        self.assertIn("broken.py", text)
        self.assertNotIn("sample.py", text)

    def test_syntax_check_unsupported_extension(self) -> None:
        text = self.call("syntax_check", {"path": "notes.txt"})
        self.assertTrue(text.startswith("错误："))

    # ---- 集成 ----
    def test_tools_are_registered_and_read_only(self) -> None:
        names = [tool.name for tool in self.workspace.tools()]
        for name in ("outline", "read_symbol", "syntax_check"):
            self.assertIn(name, names)
        for tool in self.workspace.tools():
            if tool.name in {"outline", "read_symbol", "syntax_check"}:
                self.assertFalse(tool.requires_approval)
                self.assertEqual(tool.schema()["type"], "function")

    def test_plan_mode_exposes_syntax_tools(self) -> None:
        plan = Workspace(self.root, read_only=True)
        names = [schema["function"]["name"] for schema in plan.schemas()]
        self.assertEqual(
            names,
            ["read_file", "list_dir", "grep", "outline", "read_symbol", "syntax_check"],
        )

    def test_cache_refreshes_after_edit(self) -> None:
        first = outline(self.workspace, "pkg/sample.py", depth=1)
        self.assertNotIn("def later", first)
        with self.sample.open("a", encoding="utf-8") as handle:
            handle.write("\n\ndef later():\n    return 2\n")
        second = outline(self.workspace, "pkg/sample.py", depth=1)
        self.assertIn("def later", second)

    def test_collect_symbols_ranges_and_qualnames(self) -> None:
        raw, tree = self._parse("pkg/sample.py")
        symbols = collect_symbols(raw, tree, "pkg/sample.py")
        base = next(symbol for symbol in symbols if symbol.name == "Base")
        method = next(symbol for symbol in symbols if symbol.name == "method")
        inner = next(symbol for symbol in symbols if symbol.name == "inner")
        self.assertEqual(base.kind, "类")
        self.assertEqual(method.kind, "方法")
        self.assertEqual(inner.kind, "嵌套函数")
        self.assertEqual(method.qualname, "Base.method")
        self.assertEqual(inner.qualname, "Base.method.inner")
        self.assertEqual((base.depth, method.depth, inner.depth), (0, 1, 2))
        self.assertTrue(base.start_line < method.start_line < inner.end_line)
        self.assertIsInstance(base, Symbol)

    def _parse(self, rel: str):
        from app.agent.syntax import _parse

        return _parse(self.workspace.resolve(rel))


if __name__ == "__main__":
    unittest.main(verbosity=2)
