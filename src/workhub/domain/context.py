from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    turn_id: UUID
    session_id: UUID
    employee_id: UUID
    seq: int = Field(ge=1)
    event_type: str
    text: str | None = None
    tool_call_id: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str


class SessionTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: UUID
    session_id: UUID
    employee_id: UUID
    kind: Literal["normal", "confirmation"]
    seq_lo: int = Field(ge=1)
    seq_hi: int | None = Field(default=None, ge=1)
    headline: str | None = None
    status: Literal["running", "completed", "failed"]
    pending_action_id: UUID | None = None
    created_at: str
    completed_at: str | None = None
    events: tuple[SessionEvent, ...] = ()


class ScrollWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    turns: tuple[SessionTurn, ...]
    compressed_navigation: str | None
    estimated_tokens: int = Field(ge=0)
