import hashlib
import json
from collections.abc import Mapping
from typing import Any

SENSITIVE_PARTS = frozenset(
    {
        "api_key",
        "app_secret",
        "authorization",
        "cookie",
        "headers",
        "password",
        "secret",
        "token",
    }
)
ARGUMENT_FIELDS = frozenset({"arguments", "args", "canonical_args", "tool_arguments"})


def redact_sensitive(value: Any, *, key: str | None = None) -> Any:
    normalized_key = key.lower() if key else None
    if normalized_key and any(part in normalized_key for part in SENSITIVE_PARTS):
        return "[REDACTED]"
    if normalized_key in ARGUMENT_FIELDS:
        serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        keys = sorted(str(item) for item in value) if isinstance(value, Mapping) else []
        return {"sha256": hashlib.sha256(serialized.encode()).hexdigest(), "keys": keys}
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_sensitive(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value
