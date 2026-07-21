from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple["ToolCall", ...] = ()


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    name: str
    arguments: dict[str, Any]


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class ModelSetting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_kind: Literal["chat", "embedding"]
    base_url: str
    model: str
    api_key_configured: bool
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str
