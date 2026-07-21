from uuid import uuid4

import pytest

from workhub.context import deterministic_headline, parse_headline
from workhub.domain import ActorContext
from workhub.domain.tools import inject_trusted_actor, strip_trusted_subject_fields
from workhub.runtime import SYSTEM_PROMPT, actor_prompt


def test_headline_parsing_is_single_line_bounded_and_deterministic() -> None:
    assert parse_headline({"headline": " First line \n ignored"}, "fallback") == "First line"
    assert len(parse_headline({"headline": "x" * 300}, "fallback")) == 200
    assert parse_headline({}, "  user text\nsecond") == "user text"
    assert deterministic_headline("  same input ") == deterministic_headline("  same input ")


def test_system_prompt_is_code_owned_and_actor_context_is_explicit() -> None:
    actor = ActorContext(
        employee_id=uuid4(),
        employee_no="E10001",
        display_name="Employee",
        department="Engineering",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        channel_identity_id=uuid4(),
    )

    assert "Never invent" in SYSTEM_PROMPT
    assert "clarifying question" in SYSTEM_PROMPT
    assert "Persona" in SYSTEM_PROMPT
    rendered = actor_prompt(actor)
    assert str(actor.employee_id) in rendered
    assert "timezone=Asia/Shanghai" in rendered


def test_tool_schema_and_arguments_cannot_select_trusted_actor() -> None:
    actor = ActorContext(
        employee_id=uuid4(),
        employee_no="E10001",
        display_name="Employee",
        department="Engineering",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        channel_identity_id=uuid4(),
    )
    schema = {
        "type": "object",
        "properties": {
            "employee_id": {"type": "string"},
            "idempotency_key": {"type": "string"},
            "year": {"type": "integer"},
        },
        "required": ["employee_id", "year"],
    }

    sanitized = strip_trusted_subject_fields(schema)
    assert set(sanitized["properties"]) == {"year"}
    assert sanitized["required"] == ["year"]
    assert inject_trusted_actor({"year": 2026}, actor) == {
        "year": 2026,
        "employee_id": str(actor.employee_id),
        "employee_no": "E10001",
    }

    with pytest.raises(ValueError, match="trusted subject"):
        inject_trusted_actor({"employee_id": "attacker"}, actor)
