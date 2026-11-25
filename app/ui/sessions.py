"""会话模型与持久化。

一个会话 = 一段独立的对话：聊天记录（用于重放界面）、agent 的消息历史
（用于续接上下文）、以及由用户消息派生的标题。

存储用一份 JSON 文件，放在系统的应用数据目录下。写入走「临时文件 + 原子替换」，
避免写一半崩掉把整个会话列表弄坏；读不动时一律退回空列表，界面照常启动。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict
from PyQt6.QtCore import QStandardPaths

DEFAULT_TITLE = "新会话"
_TITLE_CHARS = 24

# 反推工作区时，从内容里最多提取多少个相对路径去磁盘上验证
_MAX_PATH_TOKENS = 200
# 文件路径片段：至少一段目录 + 文件名，可带 :行号
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w/\\:@-])((?:[\w.-]+[/\\]){1,}[\w.-]+\.\w{1,8})(?::\d+)?"
)
# 目录片段：列目录的输出里全是 "Assets/"、"src/" 这种，没有文件名
_DIR_TOKEN_RE = re.compile(r"(?<![\w.@/-])([\w.-]{2,})[/\\]")


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


def _count(value: object) -> int:
    """读一个 token 计数；缺失或类型不对都当 0，坏数据不该让会话读不出来。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def same_workspace(workspace: str, root: str | Path) -> bool:
    """会话是否属于某个工作区；Windows 下路径大小写不敏感。"""
    if not workspace:
        return False
    return os.path.normcase(os.path.abspath(workspace)) == os.path.normcase(
        os.path.abspath(str(root))
    )


def _entries_text(entries: object) -> str:
    """把会话记录里的文字揉成一段，用于找路径线索。

    直接拼字符串而不是 json.dumps：转义会把换行变成 ``\\n``，
    紧贴下一行的路径前面就多了个字母，路径边界判断会被带偏。
    """
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(entries)
    return "\n".join(parts)


def _collect(pattern: re.Pattern[str], text: str) -> list[str]:
    """按正则提取不重复的片段，去重并限量。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        token = match.group(1).replace("\\", "/").strip("/")
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= _MAX_PATH_TOKENS:
            break
    return tokens


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:  # 路径过长、权限不足之类：当作没有这条线索
        return False


def _workspace_score(
    normalized_text: str, files: list[str], dirs: list[str], root: str
) -> int:
    """内容与某个工作区的吻合度；分越高越可能是在这里做的。"""
    normalized_root = os.path.normcase(os.path.abspath(str(root)).replace("/", "\\"))
    score = 50 * min(normalized_text.count(normalized_root), 20)

    name = os.path.basename(normalized_root)
    if name:
        pattern = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        score += 5 * min(len(pattern.findall(normalized_text)), 40)

    # 最硬的证据：内容里提到的文件确实长在这个目录下
    base = Path(root)
    for token in files:
        if _exists(base / token):
            score += 10
    # 稍弱一点：列目录输出里那些目录（Assets/、src/ 之类）也长在这里
    hits = 0
    for token in dirs:
        if _exists(base / token):
            hits += 1
            score += 3
            if hits >= 40:
                break
    return score


def guess_workspace(entries: list, candidates: list[str]) -> str:
    """按会话内容反推它是在哪个工作区里做的；没有线索时返回空串。

    老版本没有记录归属，只能从记录里找线索：出现过某个工作区的完整路径、
    出现过目录名，或提到的文件真的存在于那个目录下（这条最可靠）。
    分数相同时保留候选列表里靠前的那个。
    """
    if not candidates or not entries:
        return ""
    text = _entries_text(entries)
    if not text.strip():
        return ""
    normalized = os.path.normcase(text.replace("/", "\\"))
    files = _collect(_PATH_TOKEN_RE, text)
    dirs = _collect(_DIR_TOKEN_RE, text)

    best, best_score = "", 0
    for root in candidates:
        score = _workspace_score(normalized, files, dirs, root)
        if score > best_score:
            best, best_score = root, score
    return best


@dataclass
class Session:
    """一段独立的对话。"""

    id: str
    title: str = DEFAULT_TITLE
    created_at: float = 0.0
    updated_at: float = 0.0
    # 这个会话属于哪个工作区；老版本的数据没有它，启动时补上
    workspace: str = ""
    # 界面重放用的记录：("msg", role, text) / ("sys", text) /
    # ("step", title, body, tone) / ("div",)
    entries: list = field(default_factory=list)
    # messages_to_dict 的结果，恢复时直接喂回 agent
    messages: list = field(default_factory=list)
    # 模型的 token 用量：整个会话的累计，以及最近一轮的（界面显示的是后者）
    tokens_in: int = 0
    tokens_out: int = 0
    last_tokens_in: int = 0
    last_tokens_out: int = 0

    @classmethod
    def create(cls, now: float | None = None, workspace: str = "") -> "Session":
        stamp = time.time() if now is None else now
        return cls(
            id=uuid.uuid4().hex,
            created_at=stamp,
            updated_at=stamp,
            workspace=workspace,
        )

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
            "workspace": self.workspace,
            "entries": [list(entry) for entry in self.entries],
            "messages": self.messages,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "last_tokens_in": self.last_tokens_in,
            "last_tokens_out": self.last_tokens_out,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Session | None":
        if not isinstance(data, dict):
            return None
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            return None
        title = data.get("title")
        workspace = data.get("workspace")
        entries = data.get("entries")
        messages = data.get("messages")
        return cls(
            id=session_id,
            title=title if isinstance(title, str) and title else DEFAULT_TITLE,
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            workspace=workspace if isinstance(workspace, str) else "",
            entries=[list(item) for item in entries] if isinstance(entries, list) else [],
            messages=messages if isinstance(messages, list) else [],
            # 老会话文件里没有这几项，缺省就是 0
            tokens_in=_count(data.get("tokens_in")),
            tokens_out=_count(data.get("tokens_out")),
            last_tokens_in=_count(data.get("last_tokens_in")),
            last_tokens_out=_count(data.get("last_tokens_out")),
        )


class SessionStore:
    """会话列表的读写门面。"""

    # 2：会话开始记录自己所属的工作区。版本更低的文件需要补归属。
    VERSION = 2

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_store_path()
        # 上次 load() 读到的存储版本；旧文件需要重新判断会话的工作区
        self.loaded_version = self.VERSION

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[list[Session], str]:
        """读出会话与上次激活的 id；任何异常都退回空结果。"""
        # 读不到就当已经是最新格式，避免对空数据做无谓的归属推断
        self.loaded_version = self.VERSION
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

        version = data.get("version")
        self.loaded_version = version if isinstance(version, int) else 0

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
