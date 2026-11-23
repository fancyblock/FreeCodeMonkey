"""基于 tree-sitter 的 Python 语法工具：outline / read_symbol / syntax_check。

设计要点：
- 只做语法级解析，不做语义分析：没有类型推导，也没有跨文件引用索引；
- tree-sitter 的 offset 是**字节**偏移，所以必须读原始 bytes 再解析，
  不能先 decode 成 str 再 encode（BOM、换行与 CJK 都会让偏移错位）；
- 解析失败一律转成 ToolError，由 Workspace.call 统一回给模型；
- 解析结果按 (路径, mtime, 大小) 做 LRU 缓存，重复调用不重复解析；
- 三个工具都只读，因此 Plan 模式天然可用，也不触发审批。
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tools import MAX_FILE_BYTES, Tool, ToolError, Workspace

# 语言白名单：加语言只需在这里登记 grammar 模块名（其余代码是通用的）
GRAMMARS = {".py": "tree_sitter_python"}

MAX_OUTLINE_ENTRIES = 200
MAX_OUTLINE_FILES = 200
MAX_OUTLINE_CHARS = 9000
MAX_SYMBOL_LINES = 400
MAX_ERRORS = 50
SIGNATURE_CHARS = 120
TREE_CACHE_SIZE = 64

# Python grammar 里的定义节点与包装节点
DEF_NODE_TYPES = {"class_definition": "类", "function_definition": "函数"}
WRAPPER_TYPES = {"decorated_definition"}

_cache_lock = threading.Lock()
_languages: dict[str, Any] = {}
_tree_cache: "OrderedDict[tuple[str, int, int], Any]" = OrderedDict()
_availability: bool | None = None


@dataclass(frozen=True)
class Symbol:
    """一个类或函数的定义位置。"""

    path: str
    name: str
    qualname: str
    kind: str
    parent: str
    depth: int
    start_line: int
    end_line: int
    signature: str


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def available() -> bool:
    """tree-sitter 是否可用。缺少依赖时工具不注册，应用照常启动。"""
    global _availability
    if _availability is None:
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_python  # noqa: F401
        except ImportError:
            _availability = False
        else:
            _availability = True
    return _availability


def _language(ext: str) -> Any:
    with _cache_lock:
        cached = _languages.get(ext)
    if cached is not None:
        return cached
    module_name = GRAMMARS.get(ext)
    if module_name is None:
        raise ToolError(f"不支持的文件类型：{ext or '无后缀'}。当前只解析 Python（.py）。")
    try:
        import importlib

        from tree_sitter import Language

        grammar = importlib.import_module(module_name)
        language = Language(grammar.language())
    except ImportError as exc:
        raise ToolError(
            "缺少 tree-sitter 依赖：请运行 python -m pip install -r requirements.txt"
        ) from exc
    with _cache_lock:
        _languages[ext] = language
    return language


def _parse(path: Path) -> tuple[bytes, Any]:
    """读取并解析文件，返回 (原始字节, 语法树)。"""
    ext = path.suffix.lower()
    if ext not in GRAMMARS:
        raise ToolError(f"不支持的文件类型：{ext or '无后缀'}。当前只解析 Python（.py）。")
    try:
        raw = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise ToolError(f"读取失败：{exc}") from exc
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("该文件不是 UTF-8 文本（可能是二进制），无法解析") from exc

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        tree = _tree_cache.get(key)
        if tree is not None:
            _tree_cache.move_to_end(key)
            return raw, tree

    try:
        from tree_sitter import Parser

        tree = Parser(_language(ext)).parse(raw)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析器异常统一转成可读文本
        raise ToolError(f"解析失败：{type(exc).__name__}: {exc}") from exc

    with _cache_lock:
        _tree_cache[key] = tree
        while len(_tree_cache) > TREE_CACHE_SIZE:
            _tree_cache.popitem(last=False)
    return raw, tree


def _node_text(raw: bytes, node: Any) -> str:
    return raw[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _signature(raw: bytes, node: Any) -> str:
    """从定义节点头部提取单行签名：到函数体/类体开始为止，空白压成单空格。"""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    if end <= node.start_byte:
        end = node.end_byte
    text = " ".join(raw[node.start_byte : end].decode("utf-8", "replace").split())
    if len(text) > SIGNATURE_CHARS:
        text = text[:SIGNATURE_CHARS].rstrip() + "…"
    return text


def collect_symbols(raw: bytes, tree: Any, rel: str) -> list[Symbol]:
    """收集文件里的类与函数定义（含嵌套），按行号排序。"""
    symbols: list[Symbol] = []
    _collect_block(raw, tree.root_node, rel, symbols, parent="", parent_kind="module", depth=0)
    symbols.sort(key=lambda symbol: (symbol.start_line, symbol.qualname))
    return symbols


def _collect_block(
    raw: bytes,
    block: Any,
    rel: str,
    out: list[Symbol],
    *,
    parent: str,
    parent_kind: str,
    depth: int,
) -> None:
    for child in block.named_children:
        target = child
        if child.type in WRAPPER_TYPES:
            inner = child.child_by_field_name("definition")
            if inner is None:
                continue
            target = inner
        kind = DEF_NODE_TYPES.get(target.type)
        if kind is None:
            continue
        name_node = target.child_by_field_name("name")
        if name_node is None:
            continue
        name = _node_text(raw, name_node)
        if not name:
            continue
        if target.type == "function_definition":
            if parent_kind == "class":
                kind = "方法"
            elif parent_kind == "function":
                kind = "嵌套函数"
        qualname = f"{parent}.{name}" if parent else name
        body = target.child_by_field_name("body")
        out.append(
            Symbol(
                path=rel,
                name=name,
                qualname=qualname,
                kind=kind,
                parent=parent,
                depth=depth,
                start_line=child.start_point[0] + 1,
                end_line=max(child.end_point[0], target.end_point[0]) + 1,
                signature=_signature(raw, target),
            )
        )
        if body is not None:
            _collect_block(
                raw,
                body,
                rel,
                out,
                parent=qualname,
                parent_kind="class" if target.type == "class_definition" else "function",
                depth=depth + 1,
            )


def _target_files(ws: Workspace, base: Path) -> tuple[list[Path], int, bool]:
    """返回 (待解析文件, 跳过的文件数, 是否因数量上限提前停止)。

    达到文件上限就停止遍历：大仓库下没必要为了统计「跳过多少」
    把整棵目录树走完。
    """
    if base.is_file():
        if base.suffix.lower() not in GRAMMARS:
            raise ToolError(
                f"不支持的文件类型：{base.suffix or '无后缀'}。当前只解析 Python（.py）。"
            )
        return [base], 0, False

    files: list[Path] = []
    skipped = 0
    capped = False
    for candidate in ws.iter_files(base):
        if candidate.suffix.lower() not in GRAMMARS:
            continue
        if len(files) >= MAX_OUTLINE_FILES:
            capped = True
            break
        try:
            too_big = candidate.stat().st_size > MAX_FILE_BYTES
        except OSError:
            skipped += 1
            continue
        if too_big:
            skipped += 1
            continue
        files.append(candidate)
    return files, skipped, capped


def _display_text(raw: bytes) -> list[str]:
    return raw.decode("utf-8", "replace").lstrip("\ufeff").splitlines()


# ---- outline ----
def outline(ws: Workspace, path: str = ".", depth: int = 1, name_filter: str = "") -> str:
    depth = max(1, min(_as_int(depth, 1), 3))
    pattern: re.Pattern[str] | None = None
    if name_filter.strip():
        try:
            pattern = re.compile(name_filter)
        except re.error as exc:
            raise ToolError(f"name_filter 正则无效：{exc}") from exc

    base = ws.resolve(path)
    files, skipped, capped = _target_files(ws, base)
    if not files:
        raise ToolError(f"{ws.rel(base)} 下没有可解析的 Python 文件（.py）。")

    entries: list[str] = []
    total = 0
    used_chars = 0
    truncated = False
    for file in files:
        try:
            raw, tree = _parse(file)
        except ToolError as exc:
            if base.is_file():
                raise
            entries.append(f"{ws.rel(file)}  （跳过：{exc}）")
            continue
        symbols = [s for s in collect_symbols(raw, tree, ws.rel(file)) if s.depth < depth]
        if pattern is not None:
            symbols = [
                s for s in symbols if pattern.search(s.name) or pattern.search(s.qualname)
            ]
        if not symbols:
            continue
        entries.append(ws.rel(file))
        for symbol in symbols:
            if total >= MAX_OUTLINE_ENTRIES or used_chars >= MAX_OUTLINE_CHARS:
                truncated = True
                break
            line = (
                f"{'  ' * (symbol.depth + 1)}"
                f"{symbol.start_line}:{symbol.end_line}  {symbol.kind} {symbol.signature}"
            )
            entries.append(line)
            used_chars += len(line) + 1
            total += 1
        if truncated:
            break

    header = f"# {ws.rel(base)}（depth={depth}"
    if pattern is not None:
        header += f"，name_filter={name_filter!r}"
    header += f"，已解析 {len(files)} 个文件，共 {total} 个符号）"

    if not entries:
        return f"{header}\n（没有匹配的符号。可以调大 depth，或去掉 name_filter。）"

    footer: list[str] = []
    if truncated:
        footer.append(
            f"（输出已截断：上限 {MAX_OUTLINE_ENTRIES} 个符号 / {MAX_OUTLINE_CHARS} 字符，"
            "请缩小 path 或用 name_filter 过滤。）"
        )
    if capped:
        footer.append(
            f"（文件数超过 {MAX_OUTLINE_FILES} 上限，其余已跳过，请缩小 path。）"
        )
    if skipped:
        footer.append(f"（已跳过 {skipped} 个超过大小上限的文件。）")
    return "\n".join([header, *entries, *footer])


# ---- read_symbol ----
def _match_symbols(symbols: list[Symbol], wanted: str) -> list[Symbol]:
    parts = [part for part in wanted.split(".") if part]
    if not parts:
        return []
    if len(parts) == 1:
        return [symbol for symbol in symbols if symbol.name == parts[0]]
    exact = [symbol for symbol in symbols if symbol.qualname == wanted]
    if exact:
        return exact
    return [
        symbol
        for symbol in symbols
        if symbol.qualname.split(".")[-len(parts) :] == parts
    ]


def read_symbol(ws: Workspace, path: str, name: str, line: int = 0) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ToolError("缺少必填参数 name")
    target = ws.resolve(path)
    if target.is_dir():
        raise ToolError(f"{ws.rel(target)} 是目录，read_symbol 需要具体文件")
    raw, tree = _parse(target)
    rel = ws.rel(target)
    wanted = name.strip()

    matches = _match_symbols(collect_symbols(raw, tree, rel), wanted)
    if not matches:
        raise ToolError(
            f"未找到符号 {wanted}（{rel}）。可用 grep 搜索名称，或先用 outline 看结构。"
        )

    position = _as_int(line, 0)
    if position > 0:
        inside = [s for s in matches if s.start_line <= position <= s.end_line]
        if inside:
            matches = [min(inside, key=lambda s: s.end_line - s.start_line)]
        else:
            matches = [min(matches, key=lambda s: abs(s.start_line - position))]

    if len(matches) > 1:
        lines = [f"# {rel} 中 {wanted} 有 {len(matches)} 处定义，请用 line 或点号全名指定"]
        lines.extend(
            f"  {rel}:{symbol.start_line}  {symbol.kind} {symbol.qualname}"
            for symbol in matches[:20]
        )
        return "\n".join(lines)

    symbol = matches[0]
    source = _display_text(raw)
    total_lines = len(source)
    start = max(1, symbol.start_line)
    end = min(symbol.end_line, total_lines)
    clipped = min(end, start + MAX_SYMBOL_LINES - 1)
    body = "\n".join(
        f"{number:>5}\t{source[number - 1]}" for number in range(start, clipped + 1)
    )
    header = f"# {rel}:{start}-{clipped} {symbol.qualname}（{symbol.kind}，共 {end - start + 1} 行"
    if clipped < end:
        header += f"，还有 {end - clipped} 行，可配合 read_file 继续读"
    header += "）"
    return f"{header}\n{body}"


# ---- syntax_check ----
def _collect_errors(node: Any, out: list[Any], limit: int) -> None:
    if len(out) >= limit:
        return
    if node.is_error or node.is_missing:
        out.append(node)
        if node.is_error:
            return  # 不深挖错误子树，避免同一处问题刷屏
    for child in node.children:
        if len(out) >= limit:
            return
        _collect_errors(child, out, limit)


def _format_error(node: Any, source: list[str]) -> str:
    row = node.start_point[0]
    column = node.start_point[1]
    kind = "MISSING" if node.is_missing else "ERROR"
    label = f"{kind} {node.type}" if node.is_missing and node.type != "ERROR" else kind
    snippet = source[row].strip() if 0 <= row < len(source) else ""
    if len(snippet) > 100:
        snippet = snippet[:100].rstrip() + "…"
    return f"  {row + 1}:{column + 1}  {label}  {snippet}"


def check_syntax(ws: Workspace, path: str = ".", max_errors: int = 20) -> str:
    limit = max(1, min(_as_int(max_errors, 20), MAX_ERRORS))
    base = ws.resolve(path)
    files, skipped, capped = _target_files(ws, base)
    if not files:
        raise ToolError(f"{ws.rel(base)} 下没有可解析的 Python 文件（.py）。")

    reports: list[str] = []
    checked = 0
    total_errors = 0
    for file in files:
        if total_errors >= limit:
            break
        checked += 1
        try:
            raw, tree = _parse(file)
        except ToolError as exc:
            if base.is_file():
                raise
            reports.append(f"{ws.rel(file)}  （跳过：{exc}）")
            continue
        if not tree.root_node.has_error:
            continue
        source = _display_text(raw)
        nodes: list[Any] = []
        _collect_errors(tree.root_node, nodes, limit - total_errors)
        if not nodes:
            continue
        total_errors += len(nodes)
        reports.append(f"{ws.rel(file)}（{len(nodes)} 处）")
        reports.extend(_format_error(node, source) for node in nodes)

    if total_errors == 0:
        return (
            f"语法通过（检查了 {checked} 个文件）。"
            "注意：这只是语法级检查，不代表类型或行为正确。"
        )

    header = f"# 语法检查：{total_errors} 处问题（检查了 {checked} 个文件）"
    footer: list[str] = []
    if total_errors >= limit:
        footer.append(f"（已达到 {limit} 处上限，可调大 max_errors 或缩小 path。）")
    if capped:
        footer.append(
            f"（文件数超过 {MAX_OUTLINE_FILES} 上限，其余已跳过，请缩小 path。）"
        )
    if skipped:
        footer.append(f"（已跳过 {skipped} 个超过大小上限的文件。）")
    return "\n".join([header, *reports, *footer])


# ---- 工具注册 ----
def build_syntax_tools(workspace: Workspace) -> list[Tool]:
    """构造 3 个只读语法工具；依赖缺失时返回空列表（应用照常启动）。"""
    if not available():
        return []

    def run_outline(args: dict[str, Any]) -> str:
        return outline(
            workspace,
            str(args.get("path") or "."),
            _as_int(args.get("depth"), 1),
            str(args.get("name_filter") or ""),
        )

    def run_read_symbol(args: dict[str, Any]) -> str:
        return read_symbol(
            workspace,
            str(args.get("path") or ""),
            str(args.get("name") or ""),
            _as_int(args.get("line"), 0),
        )

    def run_check_syntax(args: dict[str, Any]) -> str:
        return check_syntax(
            workspace,
            str(args.get("path") or "."),
            _as_int(args.get("max_errors"), 20),
        )

    return [
        Tool(
            name="outline",
            description=(
                "用语法树列出文件或目录里的类与函数骨架（含起止行号与签名），"
                "适合快速了解结构，避免盲读全文。depth 1-3：1 只看顶层，2 含类方法与嵌套函数。"
                "name_filter 是正则，用来在工作区里定位某个符号定义在哪。"
                "仅解析 Python；结果是语法级信息，不含类型推导。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件或目录路径，省略为工作区根目录"},
                    "depth": {
                        "type": "integer",
                        "description": "展开层数 1-3，默认 1：1 顶层符号，2 含类成员",
                    },
                    "name_filter": {
                        "type": "string",
                        "description": "只显示名称匹配该正则的符号，省略为全部",
                    },
                },
                "required": [],
            },
            func=run_outline,
        ),
        Tool(
            name="read_symbol",
            description=(
                "按符号名读取整段源码（带行号），比按行号猜测更省上下文。"
                "name 支持点号全名（如 Workspace._resolve）。同名符号有多处时"
                "会返回候选，再用 line 指定所在行。仅解析 Python，语法级信息。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "name": {
                        "type": "string",
                        "description": "符号名，可用点号限定：Class.method",
                    },
                    "line": {
                        "type": "integer",
                        "description": "同名符号消歧用的行号，省略表示唯一或列出候选",
                    },
                },
                "required": ["path", "name"],
            },
            func=run_read_symbol,
        ),
        Tool(
            name="syntax_check",
            description=(
                "对文件或目录做语法检查，列出 ERROR / MISSING 节点的行号与源码片段。"
                "适合 edit_file 之后快速验证括号、缩进、拼写，不必等测试。"
                "只检查语法，不做类型检查，也不能证明运行期正确。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件或目录路径，省略为工作区根目录"},
                    "max_errors": {
                        "type": "integer",
                        "description": "最多报告多少处问题，默认 20，上限 50",
                    },
                },
                "required": [],
            },
            func=run_check_syntax,
        ),
    ]
