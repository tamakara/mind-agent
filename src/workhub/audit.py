import json
from dataclasses import dataclass
from typing import Any

import aiosqlite

from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.redaction import redact_sensitive
from workhub.storage import Database


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    summary: dict[str, Any]
    actor_type: str = "system"
    actor_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    error_code: str | None = None
    request_id: str | None = None
    duration_ms: float | None = None


class AuditWriter:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def write(self, event: AuditEvent) -> None:
        async with self.database.transaction(write=True) as connection:
            await self.write_in_transaction(connection, event)

    async def write_in_transaction(
        self, connection: aiosqlite.Connection, event: AuditEvent
    ) -> None:
        summary = json.dumps(
            redact_sensitive(event.summary),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        await connection.execute(
            """
            INSERT INTO audit_events(
                id, actor_type, actor_id, event_type, subject_type, subject_id,
                summary_json, error_code, request_id, duration_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(new_uuid4()),
                event.actor_type,
                event.actor_id,
                event.event_type,
                event.subject_type,
                event.subject_id,
                summary,
                event.error_code,
                event.request_id,
                event.duration_ms,
                format_rfc3339(utc_now()),
            ),
        )
