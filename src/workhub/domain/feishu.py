from typing import Literal

from pydantic import BaseModel, ConfigDict


class FeishuCardAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    app_id: str
    platform_user_id: str
    message_id: str
    conversation_id: str
    action_token: str
    decision: Literal["confirm", "cancel"]
