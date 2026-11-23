"""Agent 可用的文件与命令工具。

设计要点：
- 一切路径都被限制在工作区根目录内，越界直接返回可读错误，让模型自己纠正；
- 工具失败不抛异常到上层，而是返回文本错误，模型能看到并重试；
- 读类工具放行，写文件与执行命令标记为需要审批；
- 输出一律截断，避免工具结果撑爆上下文。
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_READ_LINES = 400
MAX_LIST_ENTRIES = 300
MAX_GREP_LINES = 80
MAX_OUTPUT_CHARS = 12000
MAX_FILE_BYTES = 2 * 1024 * 1024

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".workbuddy",
}

DANGER_WRITE = "write"
DANGER_EXEC = "exec"


class ToolError(Exception):
    """可预期的工具失败：消息会原样回给模型。"""


@dataclass(frozen=True)
class Tool:
    """一个工具的定义与实现。"""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[[dict[str, Any]], str] = field(repr=False)
    requires_approval: bool = False
    danger: str = ""

    def schema(self) -> dict[str, Any]:
        """OpenAI 函数调用格式，主流 provider 都能直接接受。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _require_text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"缺少必填参数 {key}")
    return value


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    tail = limit - head
    removed = len(text) - limit
    return f"{text[:head]}\n\n...（已省略 {removed} 个字符）...\n\n{text[-tail:]}"


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}K"
    return f"{size / (1024 * 1024):.1f}M"


class Workspace:
    """工作区：绑定一个根目录，并对外提供工具集合。"""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()
        if not self._root.is_dir():
            raise ValueError(f"工作目录不存在：{self._root}")
        self._tools: list[Tool] = self._build_tools()

    @property
    def root(self) -> Path:
        return self._root

    def tools(self) -> list[Tool]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools]

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self._tools if tool.name == name), None)

    def call(self, name: str, args: dict[str, Any]) -> str:
        """执行工具。任何失败都转成文本，不向调用方抛异常。"""
        tool = self.get(name)
        if tool is None:
            available = "、".join(t.name for t in self._tools)
            return f"错误：工具 {name} 不存在。可用工具：{available}"
        if not isinstance(args, dict):
            args = {}
        try:
            return _truncate(tool.func(args))
        except ToolError as exc:
            return f"错误：{exc}"
        except Exception as exc:  # noqa: BLE001 - 工具异常统一回给模型
            return f"工具执行异常：{type(exc).__name__}: {exc}"

    # ---- 路径工具 ----
    def _rel(self, path: Path) -> str:
        if path == self._root:
            return "."
        try:
            return path.relative_to(self._root).as_posix()
        except ValueError:
            return path.as_posix()

    def _resolve(self, raw: str | None, *, allow_missing: bool = False) -> Path:
        text = (raw or ".").strip() or "."
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ToolError(f"无法解析路径 {raw!r}：{exc}") from exc
        if resolved != self._root and not resolved.is_relative_to(self._root):
            raise ToolError(
                f"路径超出工作区范围：{raw}。工作区根目录是 {self._root}，"
                "只能用相对路径或工作区内的绝对路径。"
            )
        if not allow_missing and not resolved.exists():
            raise ToolError(f"路径不存在：{raw}")
        return resolved

    def _iter_files(self, base: Path) -> Iterator[Path]:
        if base.is_file():
            yield base
            return
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    # ---- 工具实现 ----
    def _read_file(self, args: dict[str, Any]) -> str:
        path = self._resolve(_require_text(args, "path"))
        if path.is_dir():
            raise ToolError(f"{self._rel(path)} 是目录，查看目录结构请用 list_dir")

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("该文件不是 UTF-8 文本（可能是二进制），无法读取") from exc
        except OSError as exc:
            raise ToolError(f"读取失败：{exc}") from exc

        lines = raw.splitlines()
        total = len(lines)
        if total == 0:
            return f"{self._rel(path)} 是空文件（0 行）"

        start = max(1, _as_int(args.get("start_line"), 1))
        end_value = _as_int(args.get("end_line"), 0)
        end = total if end_value <= 0 else min(end_value, total)
        if start > total:
            raise ToolError(f"起始行 {start} 超出文件长度（共 {total} 行）")
        if end < start:
            raise ToolError(f"结束行 {end} 小于起始行 {start}")

        # 单次最多 400 行，避免一次占满上下文
        clipped_end = min(end, start + MAX_READ_LINES - 1)
        body = "\n".join(
            f"{number:>5}\t{lines[number - 1]}" for number in range(start, clipped_end + 1)
        )
        header = f"# {self._rel(path)}（共 {total} 行，显示 {start}-{clipped_end}）"
        if clipped_end < total:
            footer = f"\n\n（还有 {total - clipped_end} 行，可用 start_line={clipped_end + 1} 继续）"
        else:
            footer = ""
        return f"{header}\n{body}{footer}"

    def _list_dir(self, args: dict[str, Any]) -> str:
        base = self._resolve(args.get("path") or ".")
        if not base.is_dir():
            raise ToolError(f"{self._rel(base)} 不是目录")
        depth = max(1, min(_as_int(args.get("depth"), 1), 3))

        lines: list[str] = [f"# {self._rel(base)}（depth={depth}）"]
        queue: list[tuple[Path, int]] = [(base, 0)]
        truncated = False
        while queue and not truncated:
            current, level = queue.pop(0)
            try:
                entries = sorted(
                    current.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
                )
            except OSError as exc:
                lines.append(f"{'  ' * level}（无法读取：{exc}）")
                continue
            indent = "  " * level
            for entry in entries:
                if len(lines) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                if entry.is_dir():
                    if entry.name in SKIP_DIRS:
                        continue
                    lines.append(f"{indent}{entry.name}/")
                    if level + 1 < depth:
                        queue.append((entry, level + 1))
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{indent}{entry.name}  {_human_size(size)}")
        if truncated:
            lines.append(f"...（超过 {MAX_LIST_ENTRIES} 项，已截断，请缩小 path 范围）")
        return "\n".join(lines)

    def _grep(self, args: dict[str, Any]) -> str:
        pattern = _require_text(args, "pattern")
        base = self._resolve(args.get("path") or ".")
        glob_filter = (args.get("glob") or "").strip()
        context = max(0, min(_as_int(args.get("context_lines"), 0), 3))
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"正则表达式无效：{exc}") from exc

        output: list[str] = []
        hit_total = 0
        truncated = False

        for path in self._iter_files(base):
            if len(output) >= MAX_GREP_LINES:
                truncated = True
                break
            if glob_filter and not self._match_glob(path, glob_filter):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            hits = [index for index, line in enumerate(lines) if regex.search(line)]
            if not hits:
                continue

            hit_total += len(hits)
            hit_set = set(hits)
            rel = self._rel(path)
            shown = sorted(
                {
                    neighbour
                    for index in hits
                    for neighbour in range(
                        max(0, index - context), min(len(lines), index + context + 1)
                    )
                }
            )
            previous: int | None = None
            for index in shown:
                if len(output) >= MAX_GREP_LINES:
                    truncated = True
                    break
                if previous is not None and index - previous > 1:
                    output.append("--")
                separator = ":" if index in hit_set else "-"
                output.append(f"{rel}{separator}{index + 1}{separator} {lines[index].rstrip()}")
                previous = index

        if hit_total == 0:
            hint = "可以缩短 pattern，或先用 list_dir 确认目录结构"
            if glob_filter:
                hint += f"，或去掉 glob={glob_filter} 再试"
            return f"未找到匹配 {pattern!r}。{hint}。"

        header = f"# 搜索 {pattern!r}：共 {hit_total} 处命中"
        footer = "\n（输出已截断，请缩小 path 或加 glob 过滤）" if truncated else ""
        return "\n".join([header, *output]) + footer

    def _match_glob(self, path: Path, pattern: str) -> bool:
        return fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(self._rel(path), pattern)

    def _edit_file(self, args: dict[str, Any]) -> str:
        raw_path = _require_text(args, "path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise ToolError("old_string 与 new_string 都必须是字符串（新建文件时 old_string 传空串）")

        path = self._resolve(raw_path, allow_missing=True)

        # 新建文件：old_string 为空且目标不存在
        if not path.exists():
            if old_string != "":
                raise ToolError(
                    f"文件不存在：{raw_path}。若要新建文件，请把 old_string 设为空字符串，"
                    "new_string 传入完整内容。"
                )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_string, encoding="utf-8")
            except OSError as exc:
                raise ToolError(f"新建失败：{exc}") from exc
            return f"已新建 {self._rel(path)}（{len(new_string.splitlines())} 行）"

        if path.is_dir():
            raise ToolError(f"{self._rel(path)} 是目录，不能编辑")
        if old_string == "":
            raise ToolError("old_string 不能为空。要整文件覆盖请先 read_file 确认内容，或改用精确替换。")

        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(f"读取失败：{exc}") from exc

        occurrences = original.count(old_string)
        if occurrences == 0:
            raise ToolError(
                "未找到 old_string。请先用 read_file 确认原文，"
                "注意缩进、空格与换行必须完全一致（建议复制原文片段）。"
            )
        if occurrences > 1:
            raise ToolError(
                f"old_string 在原文件中出现 {occurrences} 次，无法确定改哪一处。"
                "请扩大上下文，让匹配片段唯一。"
            )

        updated = original.replace(old_string, new_string, 1)
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"写入失败：{exc}") from exc

        old_lines = len(old_string.splitlines())
        new_lines = len(new_string.splitlines())
        delta = new_lines - old_lines
        sign = f"{delta:+d}" if delta else "±0"
        return f"已修改 {self._rel(path)}（行数 {sign}，当前 {len(updated.splitlines())} 行）"

    def _run_command(self, args: dict[str, Any]) -> str:
        command = _require_text(args, "command")
        timeout = max(1, min(_as_int(args.get("timeout"), 60), 600))
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"命令超过 {timeout} 秒未结束，已终止。"
                "若确实需要长时间运行，请把命令拆小，或显式传入更大的 timeout。"
            )
        except OSError as exc:
            raise ToolError(f"无法执行命令：{exc}") from exc

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parts = [f"退出码：{completed.returncode}"]
        parts.append(f"--- stdout ---\n{stdout or '(空)'}")
        if stderr:
            parts.append(f"--- stderr ---\n{stderr}")
        return "\n".join(parts)

    # ---- 注册 ----
    def _build_tools(self) -> list[Tool]:
        return [
            Tool(
                name="read_file",
                description=(
                    "读取工作区内一个文本文件，返回带行号的完整内容。"
                    "大文件请用 start_line/end_line 分段读，一次最多 400 行。"
                    "修改文件前先用它确认原文，避免编辑失败。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对工作区根目录的路径，例如 app/ui/main_window.py",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "起始行（从 1 开始），省略表示从头读",
                        },
                        "end_line": {"type": "integer", "description": "结束行（含），省略表示读到末尾"},
                    },
                    "required": ["path"],
                },
                func=self._read_file,
            ),
            Tool(
                name="list_dir",
                description=(
                    "列出目录结构，含文件名与大小。path 省略表示工作区根目录。"
                    "depth 最大 3。了解项目布局时先用它，不要猜路径。"
                    "已自动跳过 .git、.venv、node_modules 等目录。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，省略为根目录"},
                        "depth": {"type": "integer", "description": "展开层数 1-3，默认 1"},
                    },
                    "required": [],
                },
                func=self._list_dir,
            ),
            Tool(
                name="grep",
                description=(
                    "在工作区文件内容里按正则搜索，返回 文件:行号: 内容。"
                    "定位代码位置时优先用它，比逐个读文件快得多。"
                    "可用 glob 限定文件类型（如 *.py）。输出上限 80 行。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Python 正则表达式"},
                        "path": {"type": "string", "description": "搜索范围，省略为整个工作区"},
                        "glob": {
                            "type": "string",
                            "description": "文件名过滤，例如 *.py 或 **/ui/*.py",
                        },
                        "context_lines": {
                            "type": "integer",
                            "description": "命中行前后额外显示的行数 0-3，默认 0",
                        },
                    },
                    "required": ["pattern"],
                },
                func=self._grep,
            ),
            Tool(
                name="edit_file",
                description=(
                    "对文件做精确字符串替换：old_string 必须在文件中唯一出现，"
                    "否则报错，此时请扩大上下文重试。"
                    "新建文件时把 old_string 传空字符串，new_string 传完整内容。"
                    "改完建议用 run_command 跑测试或读回文件确认。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目标文件路径"},
                        "old_string": {
                            "type": "string",
                            "description": "要被替换的原文片段，必须唯一；新建文件时传空串",
                        },
                        "new_string": {"type": "string", "description": "替换后的内容"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                func=self._edit_file,
                requires_approval=True,
                danger=DANGER_WRITE,
            ),
            Tool(
                name="run_command",
                description=(
                    "在工作区根目录执行 shell 命令（Windows 下为 cmd），返回退出码与输出。"
                    "用它跑测试、构建、查依赖、看 git 状态。"
                    "禁止执行交互式命令与不可逆的破坏性操作；输出过长会被截断。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "完整命令行"},
                        "timeout": {
                            "type": "integer",
                            "description": "超时秒数，默认 60，上限 600",
                        },
                    },
                    "required": ["command"],
                },
                func=self._run_command,
                requires_approval=True,
                danger=DANGER_EXEC,
            ),
        ]
