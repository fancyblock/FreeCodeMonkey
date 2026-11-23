"""基于 QSettings 的持久化配置。

配置项以属性形式暴露，调用方无需记忆底层键名。
配置写入用户级位置（注册表 / ini），无需关心路径。
"""

from __future__ import annotations

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
