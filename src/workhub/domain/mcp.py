from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

McpToolEffect = Literal["allow", "confirm", "deny"]
PendingActionStatus = Literal["pending", "executing", "succeeded", "failed", "cancelled", "expired"]


class McpClient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: UUID
    client_key: str
    name: str
    url: AnyHttpUrl
    headers_configured: bool
    enabled: bool
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: UUID
    client_id: UUID
    client_key: str
    original_name: str
    model_name: str
    description: str | None
    input_schema: dict[str, Any]
    allowlisted: bool = False
    effect: McpToolEffect = "deny"
    revision: int = Field(default=0, ge=0)
    discovered_at: str


class ToolExecutionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor: ToolDescriptor
    canonical_arguments: dict[str, Any]
    canonical_args_json: str
    args_sha256: str
    idempotency_key: str | None = None


class PendingAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: UUID
    employee_id: UUID
    session_id: UUID
    channel_identity_id: UUID
    conversation_id: str
    card_message_id: str | None
    mcp_client_key: str
    tool_name: str
    canonical_arguments: dict[str, Any]
    args_sha256: str
    confirmation_summary: dict[str, Any]
    idempotency_key: str
    status: PendingActionStatus
    result_summary: dict[str, Any] | None
    error_code: str | None
    expires_at: str
    created_at: str
    started_at: str | None
    completed_at: str | None
