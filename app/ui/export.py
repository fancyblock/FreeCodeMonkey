"""把会话导出成 Markdown。

纯数据转换，不碰界面也不碰文件对话框：``session_to_markdown`` 之类的函数
只吃 ``Session``、吐字符串，方便直接测。落盘只有一个 :func:`write_markdown`。

导出是给人看的，不追求还原界面上的一切：
- 用户/助手消息按「加粗角色名 + 正文」排；
- agent 的执行步骤收成列表项，工具输出放进代码块，避免把正文淹掉；
- 系统提示（切换工作区之类）作为引用块，视觉上退到次要位置。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from app.ui.sessions import DEFAULT_TITLE, Session

# Windows 文件名里不允许出现的字符，外加控制字符
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名，直接当文件名会失败
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM = 60


def default_export_dir() -> Path:
    """默认导出目录：有「文档」就用它，否则用用户主目录。"""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return Path(base) if base else Path.home()


def safe_filename(title: str, fallback: str = "会话", stem_limit: int = _MAX_STEM) -> str:
    """把会话标题变成能当文件名用的主干（不含扩展名）。"""
    cleaned = _ILLEGAL_NAME_CHARS.sub("", title).strip()
    # 结尾的点和空格在 Windows 上会被静默吃掉，干脆去掉
    cleaned = cleaned.rstrip(". ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > stem_limit:
        cleaned = cleaned[:stem_limit].rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def default_export_name(session: Session) -> str:
    """单个会话的默认文件名。"""
    return f"{safe_filename(session.title or DEFAULT_TITLE)}.md"


def default_bulk_export_name(now: float | None = None) -> str:
    """导出全部会话时的默认文件名。"""
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    return f"会话导出-{stamp}.md"


def _format_time(stamp: float) -> str:
    if stamp <= 0:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def _fence(body: str) -> str:
    """选一段比正文里最长反引号串更长的围栏，免得代码块被提前闭合。"""
    ticks = "```"
    while ticks in body:
        ticks += "`"
    return ticks


def _code_block(body: str, indent: str = "") -> list[str]:
    fence = _fence(body)
    lines = [f"{indent}{fence}text"]
    for line in body.splitlines():
        lines.append(f"{indent}{line}" if line else "")
    lines.append(f"{indent}{fence}")
    return lines


def _step_markdown(title: str, body: str) -> list[str]:
    """一步 agent 操作：标题当列表项，输出跟在代码块里。"""
    lines: list[str] = []
    if title:
        lines.append(f"- {title}")
        if body:
            lines.append("")
            lines.extend(_code_block(body, indent="  "))
    elif body:
        lines.extend(_code_block(body))
    return lines


def session_to_markdown(session: Session, heading_level: int = 1) -> str:
    """把一个会话渲染成 Markdown 片段。"""
    hashes = "#" * max(1, min(6, heading_level))
    blocks: list[str] = [f"{hashes} {session.title or DEFAULT_TITLE}"]

    meta = [f"创建于 {_format_time(session.created_at)}"]
    if session.updated_at and session.updated_at >= session.created_at:
        meta.append(f"最后更新 {_format_time(session.updated_at)}")
    blocks.append("> " + " · ".join(meta))

    if not session.entries:
        blocks.append("_（这个会话还没有内容）_")
        return "\n\n".join(blocks) + "\n"

    for entry in session.entries:
        if not isinstance(entry, (list, tuple)) or not entry:
            continue
        kind = str(entry[0])
        if kind == "msg" and len(entry) >= 3:
            role = "助手" if str(entry[1]) == "assistant" else "你"
            content = "" if entry[2] is None else str(entry[2]).strip()
            if not content:
                continue
            blocks.append(f"**{role}**\n\n{content}")
        elif kind == "sys" and len(entry) >= 2:
            text = "" if entry[1] is None else str(entry[1]).strip()
            if text:
                blocks.append(f"> _{text}_")
        elif kind == "step":
            title = "" if len(entry) < 2 or entry[1] is None else str(entry[1])
            body = "" if len(entry) < 3 or entry[2] is None else str(entry[2])
            rendered = _step_markdown(title.strip(), body.rstrip())
            if rendered:
                blocks.append("\n".join(rendered))
        elif kind == "div":
            blocks.append("---")

    return "\n\n".join(blocks) + "\n"


def sessions_to_markdown(
    sessions: list[Session], now: float | None = None
) -> str:
    """把所有会话合成一份文档。

    按创建时间从早到晚排，读起来是一条时间线；会话各自带二级标题，
    与单会话导出时的标题层级对得上。
    """
    ordered = sorted(sessions, key=lambda item: item.created_at)
    stamp = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(time.time() if now is None else now)
    )
    blocks = [
        "# FreeCodeMonkey 会话导出",
        f"> 导出时间 {stamp} · 共 {len(ordered)} 个会话",
    ]
    for index, session in enumerate(ordered):
        if index:
            blocks.append("---")
        blocks.append(session_to_markdown(session, heading_level=2).rstrip())
    return "\n\n".join(blocks) + "\n"


def write_markdown(path: Path, text: str) -> Path:
    """写文件；没写扩展名时补 ``.md``。IO 异常交给调用方处理。"""
    target = Path(path)
    if target.suffix.lower() not in {".md", ".markdown"}:
        target = target.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    # 显式 UTF-8：默认编码在中文 Windows 上是 GBK，导出的中文会变乱码
    target.write_text(text, encoding="utf-8")
    return target
