import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from workhub.domain import ChannelAddress, FeishuMessage, MessageSender
from workhub.domain.feishu import FeishuCardAction


@dataclass(frozen=True, slots=True)
class MessageConversion:
    status: Literal["supported", "group", "non_text", "empty", "invalid"]
    app_id: str | None
    event_id: str | None
    message_id: str | None
    message: FeishuMessage | None = None


def convert_message_event(event: Any) -> MessageConversion:
    header = getattr(event, "header", None)
    payload = getattr(event, "event", None)
    message = getattr(payload, "message", None)
    sender = getattr(payload, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    app_id = _nonempty(getattr(header, "app_id", None))
    event_id = _nonempty(getattr(header, "event_id", None))
    message_id = _nonempty(getattr(message, "message_id", None))
    open_id = _nonempty(getattr(sender_id, "open_id", None))
    chat_id = _nonempty(getattr(message, "chat_id", None))
    if (
        app_id is None
        or event_id is None
        or message_id is None
        or open_id is None
        or chat_id is None
    ):
        return MessageConversion("invalid", app_id, event_id, message_id)
    if getattr(message, "chat_type", None) != "p2p":
        return MessageConversion("group", app_id, event_id, message_id)
    if getattr(message, "message_type", None) != "text":
        return MessageConversion("non_text", app_id, event_id, message_id)
    try:
        content = json.loads(str(getattr(message, "content", "")))
    except (TypeError, ValueError):
        return MessageConversion("invalid", app_id, event_id, message_id)
    text = content.get("text") if isinstance(content, dict) else None
    if not isinstance(text, str) or not text.strip():
        return MessageConversion("empty", app_id, event_id, message_id)
    created_ms: Any = getattr(message, "create_time", None) or getattr(
        header, "create_time", None
    )
    if not isinstance(created_ms, (str, int, float)):
        return MessageConversion("invalid", app_id, event_id, message_id)
    try:
        created_at = datetime.fromtimestamp(int(created_ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return MessageConversion("invalid", app_id, event_id, message_id)
    domain_message = FeishuMessage(
        message_id=message_id,
        event_id=event_id,
        address=ChannelAddress(
            app_id=app_id,
            conversation_type="private",
            conversation_id=chat_id,
        ),
        sender=MessageSender(platform_user_id=open_id),
        created_at=created_at,
        text=text.strip(),
    )
    return MessageConversion("supported", app_id, event_id, message_id, domain_message)


def convert_card_event(event: Any) -> FeishuCardAction | None:
    header = getattr(event, "header", None)
    payload = getattr(event, "event", None)
    operator = getattr(payload, "operator", None)
    context = getattr(payload, "context", None)
    action = getattr(payload, "action", None)
    value = getattr(action, "value", None)
    if not isinstance(value, dict):
        return None
    decision = value.get("decision")
    action_token = value.get("token")
    event_id = _nonempty(getattr(header, "event_id", None))
    app_id = _nonempty(getattr(header, "app_id", None))
    platform_user_id = _nonempty(getattr(operator, "open_id", None))
    message_id = _nonempty(getattr(context, "open_message_id", None))
    conversation_id = _nonempty(getattr(context, "open_chat_id", None))
    action_token = _nonempty(action_token)
    if (
        decision not in {"confirm", "cancel"}
        or event_id is None
        or app_id is None
        or platform_user_id is None
        or message_id is None
        or conversation_id is None
        or action_token is None
    ):
        return None
    return FeishuCardAction(
        event_id=event_id,
        app_id=app_id,
        platform_user_id=platform_user_id,
        message_id=message_id,
        conversation_id=conversation_id,
        action_token=action_token,
        decision=decision,
    )


def _nonempty(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
