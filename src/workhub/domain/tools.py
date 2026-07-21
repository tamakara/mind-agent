from copy import deepcopy
from typing import Any

from workhub.domain.employees import ActorContext

TRUSTED_SUBJECT_FIELDS = frozenset(
    {"employee_id", "employee_no", "open_id", "platform_user_id", "idempotency_key"}
)


def strip_trusted_subject_fields(schema: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(schema)
    properties = sanitized.get("properties")
    if isinstance(properties, dict):
        for field in TRUSTED_SUBJECT_FIELDS:
            properties.pop(field, None)
    required = sanitized.get("required")
    if isinstance(required, list):
        sanitized["required"] = [item for item in required if item not in TRUSTED_SUBJECT_FIELDS]
    return sanitized


def inject_trusted_actor(arguments: dict[str, Any], actor: ActorContext) -> dict[str, Any]:
    overridden = TRUSTED_SUBJECT_FIELDS.intersection(arguments)
    if overridden:
        fields = ", ".join(sorted(overridden))
        raise ValueError(f"Tool arguments must not select trusted subject fields: {fields}")
    return {
        **arguments,
        "employee_id": str(actor.employee_id),
        "employee_no": actor.employee_no,
    }
