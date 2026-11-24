"""基于 QSettings 的持久化配置。

配置项以属性形式暴露，调用方无需记忆底层键名。
配置写入用户级位置（注册表 / ini），无需关心路径。
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QByteArray, QSettings

from app.constants import APP_NAME, APP_ORG


class Settings:
    """应用配置的读写门面。"""

    def __init__(self, qsettings: QSettings | None = None) -> None:
        self._q = qsettings if qsettings is not None else QSettings(APP_ORG, APP_NAME)

    # ---- Provider 管理 ----
    @property
    def providers_json(self) -> str:
        """已保存的 provider 列表（JSON 字符串）。"""
        return self._q.value("llm/providers_json", "[]", str)

    @providers_json.setter
    def providers_json(self, value: str) -> None:
        self._q.setValue("llm/providers_json", value)

    @property
    def active_provider_id(self) -> str:
        """当前选中的 provider ID。"""
        return self._q.value("llm/active_provider_id", "", str)

    @active_provider_id.setter
    def active_provider_id(self, value: str) -> None:
        self._q.setValue("llm/active_provider_id", value)

    # ---- Agent 工作区 ----
    @property
    def workspace_root(self) -> str:
        """agent 可操作的项目根目录。"""
        return self._q.value("agent/workspace_root", "", str)

    @workspace_root.setter
    def workspace_root(self, value: str) -> None:
        self._q.setValue("agent/workspace_root", value)

    @property
    def workspace_history(self) -> list[str]:
        """最近使用过的工作目录，最近的排在前面。"""
        raw = self._q.value("agent/workspace_history_json", "[]", str)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, str) and item.strip()]

    @workspace_history.setter
    def workspace_history(self, value: list[str]) -> None:
        self._q.setValue(
            "agent/workspace_history_json",
            json.dumps(list(value), ensure_ascii=False),
        )

    # ---- 窗口状态 ----
    @property
    def geometry(self) -> QByteArray | None:
        value = self._q.value("ui/geometry")
        return value if isinstance(value, QByteArray) else None

    @geometry.setter
    def geometry(self, value: QByteArray) -> None:
        self._q.setValue("ui/geometry", value)

    def sync(self) -> None:
        """立即落盘。"""
        self._q.sync()
