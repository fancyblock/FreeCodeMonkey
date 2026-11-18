"""LLM 提供者抽象层。

设计目标：主程序只依赖 LLMProvider 接口与 ProviderRegistry，
新增一家 LLM 时只需实现一个子类并注册，无需改动界面代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """统一的模型响应结构，屏蔽各 provider 的返回差异。"""

    content: str
    model: str = ""
    provider: str = ""


class LLMProvider(ABC):
    """所有 LLM 提供者的抽象基类。

    generate 为同步阻塞调用，由异步 Worker 放到后台线程执行，
    因此这里无需（也不应）处理界面线程问题。
    """

    name: str = "base"
    display_name: str = "Base"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self.api_key = api_key
        self.model = model

    @abstractmethod
    def generate(self, prompt: str) -> LLMResponse:
        """根据提示词生成响应。子类必须实现。"""
        raise NotImplementedError


class ProviderRegistry:
    """提供者注册表：按名称登记与实例化，供配置驱动选择。"""

    _registry: dict[str, type[LLMProvider]] = {}

    @classmethod
    def register(cls, provider_cls: type[LLMProvider]) -> type[LLMProvider]:
        cls._registry[provider_cls.name] = provider_cls
        return provider_cls

    @classmethod
    def create(cls, name: str, **kwargs: object) -> LLMProvider:
        try:
            provider_cls = cls._registry[name]
        except KeyError as exc:
            available = ", ".join(cls.names())
            raise ValueError(
                f"未知的 LLM 提供者：{name!r}。可用值：{available}"
            ) from exc
        return provider_cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def display_names(cls) -> dict[str, str]:
        return {key: value.display_name for key, value in cls._registry.items()}


@ProviderRegistry.register
class EchoProvider(LLMProvider):
    """示例提供者：原样回显提示词。

    作用是在尚未接入真实 API 时，验证「输入 → 异步任务 → 显示」链路是否通畅。
    接入正式模型后可移除或保留为调试工具。
    """

    name = "echo"
    display_name = "Echo（示例）"

    def generate(self, prompt: str) -> LLMResponse:
        return LLMResponse(
            content=f"[Echo] {prompt}",
            model=self.model or "echo-1",
            provider=self.name,
        )
