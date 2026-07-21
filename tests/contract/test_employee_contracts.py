from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from workhub.domain import ActorContext, ChannelAddress, FeishuMessage, MessageSender
from workhub.domain.tools import inject_trusted_actor, strip_trusted_subject_fields


def test_feishu_message_and_actor_context_are_frozen_domain_contracts() -> None:
    actor = ActorContext(
        employee_id=uuid4(),
        employee_no="E10001",
        display_name="张三",
        department="研发部",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        channel_identity_id=uuid4(),
    )
    message = FeishuMessage(
        message_id="om_1",
        event_id="evt_1",
        address=ChannelAddress(app_id="cli_1", conversation_type="private", conversation_id="oc_1"),
        sender=MessageSender(platform_user_id="ou_1", display_name="张三"),
        created_at=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
        text="我还有多少天年假?",
    )

    assert actor.employee_no == "E10001"
    assert message.address.channel == "feishu"
    with pytest.raises(ValidationError):
        ActorContext.model_validate({**actor.model_dump(), "open_id": "ou_other"})


def test_model_tool_schema_cannot_expose_or_override_trusted_subject() -> None:
    actor = ActorContext(
        employee_id=uuid4(),
        employee_no="E10001",
        display_name="张三",
        department="研发部",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        channel_identity_id=uuid4(),
    )
    schema = {
        "type": "object",
        "properties": {
            "employee_id": {"type": "string"},
            "open_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["employee_id", "reason"],
    }

    sanitized = strip_trusted_subject_fields(schema)
    assert set(sanitized["properties"]) == {"reason"}
    assert sanitized["required"] == ["reason"]
    assert inject_trusted_actor({"reason": "vacation"}, actor)["employee_id"] == str(
        actor.employee_id
    )
    with pytest.raises(ValueError, match="trusted subject"):
        inject_trusted_actor({"employee_id": "someone-else"}, actor)
