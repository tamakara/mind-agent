from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from workhub.domain import (
    Page,
    PageRequest,
    Revision,
    format_rfc3339,
    new_uuid4,
    parse_rfc3339,
)


def test_uuid_timestamp_pagination_and_revision_contracts() -> None:
    identifier = new_uuid4()
    timestamp = datetime(2026, 7, 21, 8, 30, 15, 123456, tzinfo=UTC)

    assert identifier.version == 4
    assert format_rfc3339(timestamp) == "2026-07-21T08:30:15.123456Z"
    assert parse_rfc3339("2026-07-21T16:30:15.123456+08:00") == timestamp
    assert PageRequest().model_dump() == {"limit": 50, "offset": 0}
    assert Page[str](items=["one"], total=1, limit=50, offset=0).items == ["one"]
    assert TypeAdapter(Revision).validate_python(0) == 0


def test_common_types_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_rfc3339(datetime(2026, 7, 21))
    with pytest.raises(ValueError, match="offset"):
        parse_rfc3339("2026-07-21T08:30:15")
    with pytest.raises(ValidationError):
        PageRequest(limit=101)
    with pytest.raises(ValidationError):
        TypeAdapter(Revision).validate_python(-1)
