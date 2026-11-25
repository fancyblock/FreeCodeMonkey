"""一轮任务的文件改动记录。

判定方式：真正开始写之前拍一次快照（路径 → 修改时间 + 大小），任务结束时再拍
一次，两份快照的差异就是这一轮改动过的文件。之所以不只看 edit_file 的调用：
run_command 同样能改文件（格式化、生成代码、删文件），而命令结果里拿不到它动了
哪些文件。edit_file 另外记下增删行数，让界面能给出更具体的说明。

快照只覆盖工具能看到的文件（同一套 ``SKIP_DIRS``），``.git``、``__pycache__``、
构建产物之类不参与统计，免得任务结束时冒出无意义的噪音。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.constants import STORE_DIR_NAME

# 目录噪音：工具遍历与文件树共用这套规则，改动时两边同时生效
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
    # 会话文件也在工作区里：模型不该读到它，改动快照也不该把它算成「改了文件」
    STORE_DIR_NAME,
}

# 某一份快照：工作区相对路径 → (mtime_ns, size)
Snapshot = dict[str, tuple[int, int]]

NEW = "new"
MODIFIED = "modified"
DELETED = "deleted"
KINDS = (NEW, MODIFIED, DELETED)


@dataclass(frozen=True)
class FileChange:
    """一个文件的改动。行数只对 edit_file 的直接编辑有意义。"""

    path: str
    kind: str
    added: int = 0
    removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """存进会话记录 / 交给界面时用的纯数据形态。"""
        return {
            "path": self.path,
            "kind": self.kind,
            "added": self.added,
            "removed": self.removed,
        }


def _count(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def as_change(item: object) -> FileChange | None:
    """把一份改动（FileChange 或存下来的 dict）还原；坏数据返回 None。"""
    if isinstance(item, FileChange):
        return item
    if not isinstance(item, dict):
        return None
    path = item.get("path")
    kind = item.get("kind")
    if not isinstance(path, str) or not path or kind not in KINDS:
        return None
    return FileChange(path, str(kind), _count(item.get("added")), _count(item.get("removed")))


def snapshot(root: Path) -> Snapshot:
    """记录根目录下每个文件的大小与修改时间（纳秒）。读不到的文件直接跳过。"""
    files: Snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            files[rel] = (stat.st_mtime_ns, stat.st_size)
    return files


def changed_files(
    before: Snapshot,
    after: Snapshot,
    edits: dict[str, tuple[int, int]] | None = None,
) -> list[FileChange]:
    """对比两份快照，按「新建 → 修改 → 删除」的顺序列出改动。"""
    edits = edits or {}
    groups: dict[str, list[FileChange]] = {kind: [] for kind in KINDS}

    for path in sorted(after):
        previous = before.get(path)
        if previous is None:
            kind = NEW
        elif previous != after[path]:
            kind = MODIFIED
        else:
            continue
        added, removed = edits.get(path, (0, 0))
        groups[kind].append(FileChange(path, kind, added, removed))

    for path in sorted(set(before) - set(after)):
        groups[DELETED].append(FileChange(path, DELETED))

    return [change for kind in KINDS for change in groups[kind]]
