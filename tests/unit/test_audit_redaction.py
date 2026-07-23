import json

from workhub.redaction import redact_sensitive


def test_audit_redaction_removes_credentials_tokens_and_complete_arguments() -> None:
    redacted = redact_sensitive(
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
