"""会话模型与持久化。

一个会话 = 一段独立的对话：聊天记录（用于重放界面）、agent 的消息历史
（用于续接上下文）、以及由用户消息派生的标题。

存储跟着工作区走：每个工作区目录下有一个 ``.free-code-monkey/sessions.json``，
只装这个工作区的会话。因此会话归属由「文件在哪」天然决定，不需要反推；
把项目目录拷走或备份，会话也就一起带走了。

写入走「临时文件 + 原子替换」，避免写一半崩掉把整个会话列表弄坏；读不动时
一律退回空列表，界面照常启动。工作区不可写（只读盘、权限不足、网络盘掉线）时
退回应用数据目录下的同名副本，读写照旧，不会静默丢会话。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict
from PyQt6.QtCore import QStandardPaths

from app.constants import STORE_DIR_NAME

DEFAULT_TITLE = "新会话"
STORE_FILE_NAME = "sessions.json"
_TITLE_CHARS = 24

# 保存结果：写进了工作区、退回了应用数据目录、还是两边都写不进去
SAVED_PRIMARY = "primary"
SAVED_FALLBACK = "fallback"
SAVE_FAILED = "failed"

# 反推工作区时，从内容里最多提取多少个相对路径去磁盘上验证
_MAX_PATH_TOKENS = 200
# 文件路径片段：至少一段目录 + 文件名，可带 :行号
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w/\\:@-])((?:[\w.-]+[/\\]){1,}[\w.-]+\.\w{1,8})(?::\d+)?"
)
# 目录片段：列目录的输出里全是 "Assets/"、"src/" 这种，没有文件名
_DIR_TOKEN_RE = re.compile(r"(?<![\w.@/-])([\w.-]{2,})[/\\]")


def _app_data_dir() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    if not base:
        base = str(Path.home() / STORE_DIR_NAME)
    return Path(base)


def legacy_store_path() -> Path:
    """旧版全局会话文件的位置：所有工作区的会话混在一个文件里，只读保留作迁移源。"""
    return _app_data_dir() / STORE_FILE_NAME


def workspace_store_path(root: Path | str) -> Path:
    """某个工作区自己的会话文件：``<root>/.free-code-monkey/sessions.json``。"""
    return Path(root).expanduser().resolve() / STORE_DIR_NAME / STORE_FILE_NAME


def _workspace_key(root: Path | str) -> str:
    """把工作区路径压成一个可读又唯一的目录名：目录名 + 路径哈希。"""
    normalized = os.path.normcase(os.path.abspath(str(root)))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    name = Path(normalized).name or "workspace"
    # 目录名里可能有空格或非 ASCII，截短并去掉不适合当路径的成分
    name = re.sub(r"[^\w.-]+", "-", name).strip("-.") or "workspace"
    return f"{name[:40]}-{digest}"


def fallback_store_path(root: Path | str) -> Path:
    """工作区不可写时的落点：应用数据目录下按工作区分开的副本。"""
    return _app_data_dir() / "workspaces" / _workspace_key(root) / STORE_FILE_NAME


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
    """某个工作区的会话读写门面。

    主位置是工作区目录下的隐藏目录；工作区写不进去时退回应用数据目录。
    读的时候两处都读、按 id 合并，覆盖「之前不可写、现在可写了」这类来回切换。
    """

    # 3：会话改为按工作区存放，文件里的会话天然属于这个目录
    VERSION = 3

    def __init__(
        self,
        root: Path | str,
        *,
        legacy_path: Path | str | None = None,
        fallback_path: Path | str | None = None,
        ledger_path: Path | str | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._legacy = (
            Path(legacy_path) if legacy_path is not None else legacy_store_path()
        )
        self._fallback = (
            Path(fallback_path)
            if fallback_path is not None
            else fallback_store_path(self._root)
        )
        self._ledger = (
            Path(ledger_path) if ledger_path is not None else legacy_ledger_path()
        )
        # 最近一次 save() 的落点，写回退位置时界面要提示一次
        self.last_result = SAVED_PRIMARY

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        """主存储位置（工作区里那份）。"""
        return workspace_store_path(self._root)

    @property
    def fallback_path(self) -> Path:
        return self._fallback

    @property
    def legacy_path(self) -> Path:
        return self._legacy

    @property
    def ledger_path(self) -> Path:
        return self._ledger

    def exists(self) -> bool:
        """本工作区是否已经有自己的会话文件；迁移前用它判断要不要认领旧数据。"""
        return self.path.is_file()

    def load(self) -> tuple[list[Session], str]:
        """读出本工作区的会话与上次激活的 id；任何异常都退回空结果。"""
        primary_sessions, primary_active = _read_store(self.path)
        # 主位置有数据就以它为准；回退位置只在主位置读不到时补位，
        # 合并时按 id 去重、主位置优先，避免同一轮会话出现两次
        if primary_sessions:
            extra, extra_active = _read_store(self._fallback)
            known = {session.id for session in primary_sessions}
            primary_sessions.extend(
                session for session in extra if session.id not in known
            )
            primary_sessions.sort(key=lambda item: item.created_at, reverse=True)
            return self._adopt(primary_sessions), primary_active or extra_active

        fallback_sessions, fallback_active = (
            _read_store(self._fallback) if self._fallback.is_file() else ([], "")
        )
        return self._adopt(fallback_sessions), fallback_active

    def _adopt(self, sessions: list[Session]) -> list[Session]:
        """文件在哪个工作区，里面的会话就属于哪个工作区。

        文件被手工拷到别的项目、或旧数据缺归属时会派上用场，省得会话因为
        「workspace 对不上」而在列表里凭空消失。
        """
        for session in sessions:
            session.workspace = str(self._root)
        return sessions

    def save(self, sessions: list[Session], active_id: str) -> str:
        """整体落盘并返回落点；任何情况下都不抛异常。

        保存前把每个会话的 ``workspace`` 盖成当前根目录：会话在文件之间移动过之后，
        归属字段会跟着走，不会出现「记的是 A、文件却在 B」。
        """
        for session in sessions:
            session.workspace = str(self._root)
        payload = {
            "version": self.VERSION,
            "active": active_id,
            "sessions": [session.to_dict() for session in sessions],
        }
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            self.last_result = SAVE_FAILED
            return self.last_result

        if _write_store(self.path, text):
            self.last_result = SAVED_PRIMARY
            return self.last_result
        # 工作区不可写（只读盘、权限不足、网络盘掉线）：退回应用数据目录
        if _write_store(self._fallback, text):
            self.last_result = SAVED_FALLBACK
            return self.last_result
        self.last_result = SAVE_FAILED
        return self.last_result


def _read_store(path: Path) -> tuple[list[Session], str]:
    """读一份会话文件；缺失、读不动或结构不对都当空。"""
    try:
        raw = path.read_text(encoding="utf-8")
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


def _write_store(path: Path, text: str) -> bool:
    """原子写一份会话文件；返回是否写成功。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 磁盘满、只读盘、无权限之类：交给调用方决定回退
        return False
    return True


def legacy_ledger_path() -> Path:
    """迁移台账：记下旧文件里每轮会话判给了哪个工作区。

    旧文件是只读的，而各工作区是分别打开的——没有这份台账，那些内容里
    毫无线索的旧会话会被每个工作区各认领一次，变成重复记录。
    """
    return _app_data_dir() / "legacy_claims.json"


def _read_ledger(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _write_ledger(path: Path, claims: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 台账写不进去只影响本次判断，不该阻断迁移
        pass


def migrate_legacy_sessions(
    root: Path | str,
    candidates: list[str],
    legacy_path: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> list[Session]:
    """从旧版全局文件里挑出属于 ``root`` 的会话；旧文件只读，绝不改写。

    旧数据里有些会话没记工作区，只能按内容反推（``guess_workspace``）；
    推断不出又完全没有归属的，跟先打开的那个工作区走。判断结果记进台账，
    后面的工作区就按同一份结论走，不会重复认领。
    """
    path = Path(legacy_path) if legacy_path is not None else legacy_store_path()
    legacy_sessions, _active = _read_store(path)
    if not legacy_sessions:
        return []

    ledger_file = (
        Path(ledger_path) if ledger_path is not None else legacy_ledger_path()
    )
    claims = _read_ledger(ledger_file)
    target_root = Path(root).expanduser().resolve()

    claimed: list[Session] = []
    changed = False
    for session in legacy_sessions:
        decided = claims.get(session.id)
        if decided is None:
            # 头一次遇到这一轮：先看它自己记的归属，再按内容反推，都不行才归当前工作区
            decided = session.workspace or guess_workspace(session.entries, candidates)
            if not decided:
                decided = str(target_root)
            claims[session.id] = decided
            changed = True
        if same_workspace(decided, target_root):
            session.workspace = str(target_root)
            claimed.append(session)

    if changed:
        _write_ledger(ledger_file, claims)
    return claimed
