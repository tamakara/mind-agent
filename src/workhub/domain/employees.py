from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Employee(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: UUID
    employee_no: str
    display_name: str
    department: str
    manager_employee_id: UUID | None
    timezone: str
    status: Literal["active", "disabled"]
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class ChannelIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity_id: UUID
    channel: Literal["feishu"]
    app_id: str
    platform_user_id: str
    display_name: str | None
    binding_status: Literal["unbound", "bound"]
    employee_id: UUID | None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class ActorContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: UUID
    employee_no: str
    display_name: str
    department: str
    manager_employee_id: UUID | None
    timezone: str
    channel_identity_id: UUID


class IdentityResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["unbound", "disabled", "active"]
    identity: ChannelIdentity
    actor: ActorContext | None = None
    session_id: UUID | None = None
