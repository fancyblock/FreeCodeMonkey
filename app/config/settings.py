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

    # ---- LLM 配置 ----
    @property
    def provider(self) -> str:
        return self._q.value("llm/provider", "echo", str)

    @provider.setter
    def provider(self, value: str) -> None:
        self._q.setValue("llm/provider", value)

    @property
    def api_key(self) -> str:
        return self._q.value("llm/api_key", "", str)

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._q.setValue("llm/api_key", value)

    @property
    def model(self) -> str:
        return self._q.value("llm/model", "", str)

    @model.setter
    def model(self, value: str) -> None:
        self._q.setValue("llm/model", value)

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
