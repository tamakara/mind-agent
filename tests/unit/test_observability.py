from workhub.redaction import redact_sensitive


def test_redact_removes_nested_sensitive_values() -> None:
    value = {
        "api_key": "secret-key",
        "nested": {"Authorization": "Bearer credential", "ordinary": "visible"},
        "items": [{"action_token": "card-token"}],
    }

    assert redact_sensitive(value) == {
        "api_key": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]", "ordinary": "visible"},
        "items": [{"action_token": "[REDACTED]"}],
    }
