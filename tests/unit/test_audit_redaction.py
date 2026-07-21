import json

from workhub.audit import redact_audit_value


def test_audit_redaction_removes_credentials_tokens_and_complete_arguments() -> None:
    redacted = redact_audit_value(
        {
            "password": "administrator-password",
            "headers": {"Authorization": "Bearer credential"},
            "action_token": "card-token",
            "arguments": {"reason": "private reason", "days": 2},
            "result": {"request_id": "LV-1"},
        }
    )
    serialized = json.dumps(redacted)

    assert "administrator-password" not in serialized
    assert "Bearer credential" not in serialized
    assert "card-token" not in serialized
    assert "private reason" not in serialized
    assert redacted["password"] == "[REDACTED]"
    assert redacted["arguments"]["keys"] == ["days", "reason"]
    assert redacted["result"] == {"request_id": "LV-1"}
