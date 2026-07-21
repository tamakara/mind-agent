from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelAddress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: Literal["feishu"] = "feishu"
    app_id: str
    conversation_type: Literal["private", "group"]
    conversation_id: str


class MessageSender(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    platform_user_id: str
    display_name: str | None = None


class FeishuMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    event_id: str
    address: ChannelAddress
    sender: MessageSender
    created_at: datetime
    text: str = Field(min_length=1)
