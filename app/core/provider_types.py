"""Provider 类型定义与字段元数据。

新增一类 provider 时，在此登记类型、表单字段与模型工厂即可，
界面层会根据字段元数据自动生成表单，无需改动 UI 代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI


@dataclass(frozen=True)
class FieldSpec:
    """一个表单字段的规格。"""

    name: str
    label: str
    required: bool = True
    secret: bool = False
    placeholder: str = ""
    default: str = ""


@dataclass(frozen=True)
class ProviderTypeSpec:
    """一种 provider 类型的完整规格。"""

    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    model_factory: Callable[[dict[str, str]], BaseChatModel]
    # 用于连接验证的测试消息
    probe_message: str = "hi"


def _openai_factory(values: dict[str, str]) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "model": values["model"],
        "api_key": values["api_key"],
        "temperature": 0,
    }
    if values.get("base_url"):
        kwargs["base_url"] = values["base_url"].rstrip("/")
    return ChatOpenAI(**kwargs)


def _anthropic_factory(values: dict[str, str]) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "model_name": values["model"],
        "api_key": values["api_key"],
        "temperature": 0,
    }
    # Anthropic 兼容接口（如代理）可自定义 base_url
    return ChatAnthropic(**kwargs)


def _ollama_factory(values: dict[str, str]) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "model": values["model"],
        "temperature": 0,
    }
    if values.get("base_url"):
        kwargs["base_url"] = values["base_url"].rstrip("/")
    return ChatOllama(**kwargs)


PROVIDER_TYPES: dict[str, ProviderTypeSpec] = {
    "openai_compatible": ProviderTypeSpec(
        key="openai_compatible",
        label="OpenAI 兼容接口",
        fields=(
            FieldSpec("name", "名称", placeholder="例如：DeepSeek"),
            FieldSpec("base_url", "Base URL", required=False,
                      placeholder="https://api.deepseek.com/v1"),
            FieldSpec("api_key", "API Key", secret=True,
                      placeholder="sk-..."),
            FieldSpec("model", "模型", placeholder="deepseek-chat"),
            FieldSpec("context_window", "上下文窗口（tokens）", required=False,
                      default="128000", placeholder="128000"),
        ),
        model_factory=_openai_factory,
    ),
    "anthropic": ProviderTypeSpec(
        key="anthropic",
        label="Anthropic (Claude)",
        fields=(
            FieldSpec("name", "名称", placeholder="例如：Claude"),
            FieldSpec("api_key", "API Key", secret=True,
                      placeholder="sk-ant-..."),
            FieldSpec("model", "模型", placeholder="claude-sonnet-4-5"),
            FieldSpec("context_window", "上下文窗口（tokens）", required=False,
                      default="200000", placeholder="200000"),
        ),
        model_factory=_anthropic_factory,
    ),
    "ollama": ProviderTypeSpec(
        key="ollama",
        label="Ollama（本地）",
        fields=(
            FieldSpec("name", "名称", placeholder="例如：本地 Ollama"),
            FieldSpec("base_url", "服务地址", required=False,
                      default="http://localhost:11434",
                      placeholder="http://localhost:11434"),
            FieldSpec("model", "模型", placeholder="qwen2.5-coder"),
            FieldSpec("context_window", "上下文窗口（tokens）", required=False,
                      default="32768", placeholder="32768"),
        ),
        model_factory=_ollama_factory,
    ),
}


def get_spec(type_key: str) -> ProviderTypeSpec:
    try:
        return PROVIDER_TYPES[type_key]
    except KeyError as exc:
        raise ValueError(f"未知的 provider 类型：{type_key!r}") from exc
