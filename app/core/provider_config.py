"""Provider 配置模型：单条连接配置的持久化表示。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from app.constants import DEFAULT_CONTEXT_WINDOW
from app.core.provider_types import get_spec


@dataclass
class ProviderConfig:
    """一条已保存的 provider 配置。

    id       稳定唯一标识，用于选择当前使用的配置
    type     provider 类型键，对应 PROVIDER_TYPES
    values   表单字段值，键与 FieldSpec.name 对应（含 name）
    """

    id: str
    type: str
    values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, type_key: str, values: dict[str, str]) -> "ProviderConfig":
        return cls(id=uuid.uuid4().hex, type=type_key, values=dict(values))

    @property
    def name(self) -> str:
        return self.values.get("name", "").strip() or "(未命名)"

    @property
    def context_window(self) -> int:
        """模型上下文窗口（tokens）：用户填了就用，否则回落到类型默认值。"""
        raw = self.values.get("context_window", "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                value = 0
            if value > 0:
                return value
        default = next(
            (f.default for f in get_spec(self.type).fields if f.name == "context_window"),
            "",
        )
        try:
            value = int(default)
        except ValueError:
            return DEFAULT_CONTEXT_WINDOW
        return value if value > 0 else DEFAULT_CONTEXT_WINDOW

    def validate(self) -> None:
        """校验必填字段。API Key 仅对需要密钥的类型校验。"""
        spec = get_spec(self.type)
        for f in spec.fields:
            # ollama 不需要 API Key，字段元数据中本就不含该字段，无需特殊处理
            if f.required and not self.values.get(f.name, "").strip():
                raise ValueError(f"[{self.name}] 缺少必填项：{f.label}")

    def masked(self) -> dict[str, object]:
        """返回脱敏表示，用于日志/调试输出。"""
        data: dict[str, object] = {"id": self.id, "type": self.type, "values": {}}
        for key, value in self.values.items():
            spec_field = next((f for f in get_spec(self.type).fields if f.name == key), None)
            if spec_field and spec_field.secret:
                data["values"][key] = "****" if value else ""
            else:
                data["values"][key] = value
        return data

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "type": self.type, "values": dict(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ProviderConfig":
        return cls(
            id=str(data.get("id", uuid.uuid4().hex)),
            type=str(data.get("type", "")),
            values={str(k): str(v) for k, v in dict(data.get("values", {})).items()},  # type: ignore[arg-type]
        )


class ProviderStore:
    """Provider 列表的 JSON 编解码。"""

    @staticmethod
    def loads(raw: str) -> list[ProviderConfig]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        result: list[ProviderConfig] = []
        for item in data:
            if isinstance(item, dict):
                try:
                    config = ProviderConfig.from_dict(item)
                    # 跳过类型已不存在的旧配置
                    get_spec(config.type)
                    result.append(config)
                except ValueError:
                    continue
        return result

    @staticmethod
    def dumps(configs: list[ProviderConfig]) -> str:
        return json.dumps([c.to_dict() for c in configs], ensure_ascii=False)
