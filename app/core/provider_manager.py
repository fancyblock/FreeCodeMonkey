"""Provider 管理器：配置的增删改查、持久化、模型创建与连接验证。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.config import Settings
from app.core.provider_config import ProviderConfig, ProviderStore
from app.core.provider_types import get_spec


class ConnectionError(Exception):
    """连接验证失败。"""


class ProviderManager:
    """管理所有已保存的 provider 配置。

    所有方法均在界面线程调用；build_model / test_connection 中的
    阻塞部分由调用方放到后台 Worker 执行。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: list[ProviderConfig] = ProviderStore.loads(
            settings.providers_json
        )

    # ---- 配置集合 ----
    @property
    def providers(self) -> list[ProviderConfig]:
        return list(self._providers)

    def get(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self._providers if p.id == provider_id), None)

    def add(self, config: ProviderConfig) -> None:
        config.validate()
        self._providers.append(config)
        self._persist()

    def update(self, config: ProviderConfig) -> None:
        config.validate()
        for index, existing in enumerate(self._providers):
            if existing.id == config.id:
                self._providers[index] = config
                self._persist()
                return
        raise ValueError(f"未找到 provider：{config.id}")

    def remove(self, provider_id: str) -> None:
        self._providers = [p for p in self._providers if p.id != provider_id]
        if self._settings.active_provider_id == provider_id:
            self.active_id = ""
        self._persist()

    # ---- 当前选中项 ----
    @property
    def active_id(self) -> str:
        active = self._settings.active_provider_id
        return active if self.get(active) is not None else ""

    @active_id.setter
    def active_id(self, value: str) -> None:
        self._settings.active_provider_id = value
        self._settings.sync()

    @property
    def active_config(self) -> ProviderConfig | None:
        current = self.active_id
        return self.get(current) if current else None

    # ---- 持久化 ----
    def _persist(self) -> None:
        self._settings.providers_json = ProviderStore.dumps(self._providers)
        self._settings.sync()

    # ---- 模型与验证 ----
    def build_model(self, config: ProviderConfig) -> BaseChatModel:
        """根据配置构造 LangChain ChatModel。"""
        config.validate()
        spec = get_spec(config.type)
        return spec.model_factory(config.values)

    def test_connection(self, config: ProviderConfig) -> str:
        """发送最短测试消息验证连接是否可用。成功时返回模型回复摘要。"""
        config.validate()
        model = self.build_model(config)
        spec = get_spec(config.type)
        try:
            response = model.invoke([HumanMessage(content=spec.probe_message)])
        except Exception as exc:  # noqa: BLE001 - 需捕获所有 provider 异常
            raise ConnectionError(f"{type(exc).__name__}: {exc}") from exc

        content = response.content
        if isinstance(content, list):
            # 部分模型返回内容块列表，提取其中的文本
            text_parts = [
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            ]
            content = "".join(text_parts)
        content = str(content).strip().replace("\n", " ")
        if not content:
            content = "(空响应)"
        return content[:120]
