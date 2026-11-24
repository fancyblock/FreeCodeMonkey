"""会话模型与持久化。

一个会话 = 一段独立的对话：聊天记录（用于重放界面）、agent 的消息历史
（用于续接上下文）、以及由用户消息派生的标题。

存储用一份 JSON 文件，放在系统的应用数据目录下。写入走「临时文件 + 原子替换」，
避免写一半崩掉把整个会话列表弄坏；读不动时一律退回空列表，界面照常启动。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict
from PyQt6.QtCore import QStandardPaths

DEFAULT_TITLE = "新会话"
_TITLE_CHARS = 24


def default_store_path() -> Path:
    """会话文件的默认位置（Windows 下在 AppData 里）。"""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    if not base:
        base = str(Path.home() / ".free-code-monkey")
    return Path(base) / "sessions.json"


def make_title(text: str) -> str:
    """用第一条用户消息生成会话标题，折叠空白并截断。"""
    preview = " ".join(text.split())
    if len(preview) > _TITLE_CHARS:
        preview = preview[:_TITLE_CHARS] + "…"
    return preview or DEFAULT_TITLE


@dataclass
class Session:
    """一段独立的对话。"""

    id: str
    title: str = DEFAULT_TITLE
    created_at: float = 0.0
    updated_at: float = 0.0
    # 界面重放用的记录：("msg", role, text) / ("sys", text) /
    # ("step", title, body, tone) / ("div",)
    entries: list = field(default_factory=list)
    # messages_to_dict 的结果，恢复时直接喂回 agent
    messages: list = field(default_factory=list)

    @classmethod
    def create(cls, now: float | None = None) -> "Session":
        stamp = time.time() if now is None else now
        return cls(id=uuid.uuid4().hex, created_at=stamp, updated_at=stamp)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def history(self) -> list[BaseMessage]:
        """还原成 LangChain 消息；数据坏掉时当作没有历史，不让界面崩。"""
        if not self.messages:
            return []
        try:
            return messages_from_dict(self.messages)
        except (ValueError, TypeError, KeyError):
            return []

    def set_history(self, messages: list[BaseMessage]) -> None:
        try:
            self.messages = messages_to_dict(messages)
        except (ValueError, TypeError):
            self.messages = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [list(entry) for entry in self.entries],
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Session | None":
        if not isinstance(data, dict):
            return None
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            return None
        title = data.get("title")
        entries = data.get("entries")
        messages = data.get("messages")
        return cls(
            id=session_id,
            title=title if isinstance(title, str) and title else DEFAULT_TITLE,
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            entries=[list(item) for item in entries] if isinstance(entries, list) else [],
            messages=messages if isinstance(messages, list) else [],
        )


class SessionStore:
    """会话列表的读写门面。"""

    VERSION = 1

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_store_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[list[Session], str]:
        """读出会话与上次激活的 id；任何异常都退回空结果。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], ""
        try:
            data = json.loads(raw)
        except ValueError:
            return [], ""
        if not isinstance(data, dict):
            return [], ""

        sessions: list[Session] = []
        for item in data.get("sessions") or []:
            session = Session.from_dict(item)
            if session is not None:
                sessions.append(session)
        # 新的在上面，和界面上的顺序保持一致
        sessions.sort(key=lambda item: item.created_at, reverse=True)
        active = data.get("active")
        return sessions, active if isinstance(active, str) else ""

    def save(self, sessions: list[Session], active_id: str) -> None:
        """整体落盘；写失败不抛异常，界面不该因为存不下而崩掉。"""
        payload = {
            "version": self.VERSION,
            "active": active_id,
            "sessions": [session.to_dict() for session in sessions],
        }
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            # 磁盘满、无权限之类：本次不保存，下次改动再试
            pass
