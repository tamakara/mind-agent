import pytest

from workhub.errors import ApplicationError
from workhub.mcp.manager import validate_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["annual", "sick"]},
        "days": {"type": "integer", "minimum": 1},
        "reason": {"type": "string", "minLength": 3},
        "periods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"date": {"type": "string"}},
                "required": ["date"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kind", "days", "reason"],
    "additionalProperties": False,
}


def test_mcp_arguments_use_complete_json_schema_validation() -> None:
    validate_arguments(
        SCHEMA,
        {
            "kind": "annual",
            "days": 2,
            "reason": "family trip",
            "periods": [{"date": "2026-08-01"}],
        },
    )

    with pytest.raises(ApplicationError) as captured:
        validate_arguments(
            SCHEMA,
            {
                "kind": "other",
                "days": 0,
                "reason": "x",
                "periods": [{"date": "2026-08-01", "employee_id": "forged"}],
            },
        )

    assert captured.value.code == "invalid_tool_arguments"
    assert len(captured.value.details or []) == 4


def test_mcp_arguments_must_be_an_object() -> None:
    with pytest.raises(ApplicationError) as captured:
        validate_arguments(SCHEMA, [])  # type: ignore[arg-type]

    assert captured.value.code == "invalid_tool_arguments"
