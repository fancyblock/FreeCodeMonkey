"""模型调用的 token 用量。

优先用 provider 回报的真实用量（LangChain 的 ``usage_metadata``，或各家塞在
``response_metadata`` 里的 ``token_usage`` / ``usage``）；拿不到时按字符估算兜底，
并标明是估算值——界面上把估算当成账单看就不好了。

这里只做「读」：调用方负责把每轮的结果累加起来。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 估算值的标记，界面上跟在数字前面
APPROXIMATE_MARK = "≈"


def _count(value: object) -> int | None:
    """把 provider 给的数字读成非负整数；类型不对或为负时算作没给。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number >= 0 else None


def _pair(source: Any, input_key: str, output_key: str) -> tuple[int, int] | None:
    if not isinstance(source, dict):
        return None
    tokens_in = _count(source.get(input_key))
    tokens_out = _count(source.get(output_key))
    if tokens_in is None or tokens_out is None:
        return None
    return tokens_in, tokens_out


def message_usage(message: Any) -> tuple[int, int] | None:
    """从一条模型回复里读出 (输入, 输出) token；provider 没给就返回 None。"""
    usage = _pair(getattr(message, "usage_metadata", None), "input_tokens", "output_tokens")
    if usage is not None:
        return usage

    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    # OpenAI 系：prompt / completion
    usage = _pair(metadata.get("token_usage"), "prompt_tokens", "completion_tokens")
    if usage is not None:
        return usage
    # Anthropic 系：input / output
    return _pair(metadata.get("usage"), "input_tokens", "output_tokens")


def compact_tokens(count: int) -> str:
    """把 token 数压成下拉框旁边放得下的一小串：1234 → 1.2k。"""
    value = max(0, int(count))
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    text = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{text}m"


@dataclass
class TokenUsage:
    """一段工作的 token 用量：输入累加 + 输出累加。

    ``approximate`` 一旦为真就保持为真：只要有一次是估算的，合计值就只能是估算。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    approximate: bool = False

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, input_tokens: int, output_tokens: int, approximate: bool = False) -> None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.approximate = self.approximate or approximate

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "approximate": self.approximate,
        }


def as_usage(payload: object) -> TokenUsage | None:
    """把存下来的 dict 还原成 TokenUsage；坏数据返回 None。"""
    if isinstance(payload, TokenUsage):
        return payload
    if not isinstance(payload, dict):
        return None
    tokens_in = _count(payload.get("input_tokens"))
    tokens_out = _count(payload.get("output_tokens"))
    if tokens_in is None or tokens_out is None:
        return None
    return TokenUsage(tokens_in, tokens_out, bool(payload.get("approximate")))
