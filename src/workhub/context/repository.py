import json
from typing import Any, Literal, cast
from uuid import UUID

import aiosqlite

from workhub.domain import ActorContext, format_rfc3339, new_uuid4, utc_now
from workhub.domain.context import SessionEvent, SessionTurn
from workhub.errors import ApplicationError
from workhub.storage import Database

TurnKind = Literal["normal", "confirmation"]


class ScrollRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start_turn(
        self,
        actor: ActorContext,
        session_id: UUID,
        *,
        kind: TurnKind,
        first_event_type: str,
        first_text: str,
        pending_action_id: UUID | None = None,
    ) -> SessionTurn:
        turn_id = new_uuid4()
        event_id = new_uuid4()
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            seq = await _claim_seq(connection, actor.employee_id, session_id, timestamp)
            await connection.execute(
                """
                INSERT INTO session_turns(
                    id, session_id, employee_id, kind, seq_lo, status,
                    pending_action_id, created_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    str(turn_id),
                    str(session_id),
                    str(actor.employee_id),
                    kind,
                    seq,
                    str(pending_action_id) if pending_action_id else None,
                    timestamp,
                ),
            )
            await _insert_event(
                connection,
                event_id=event_id,
                turn_id=turn_id,
                session_id=session_id,
                employee_id=actor.employee_id,
                seq=seq,
                event_type=first_event_type,
                text=first_text,
                tool_call_id=None,
                payload=None,
                timestamp=timestamp,
            )
        return await self.get_turn(actor, session_id, turn_id)

    async def append_event(
        self,
        actor: ActorContext,
        session_id: UUID,
        turn_id: UUID,
        *,
        event_type: str,
        text: str | None = None,
        tool_call_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SessionEvent:
        event_id = new_uuid4()
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            await _require_running_turn(connection, actor.employee_id, session_id, turn_id)
            seq = await _claim_seq(connection, actor.employee_id, session_id, timestamp)
            await _insert_event(
                connection,
                event_id=event_id,
                turn_id=turn_id,
                session_id=session_id,
                employee_id=actor.employee_id,
                seq=seq,
                event_type=event_type,
                text=text,
                tool_call_id=tool_call_id,
                payload=payload,
                timestamp=timestamp,
            )
        return SessionEvent(
            event_id=event_id,
            turn_id=turn_id,
            session_id=session_id,
            employee_id=actor.employee_id,
            seq=seq,
            event_type=event_type,
            text=text,
            tool_call_id=tool_call_id,
            payload=payload,
            created_at=timestamp,
        )

    async def complete_turn(
        self,
        actor: ActorContext,
        session_id: UUID,
        turn_id: UUID,
        *,
        headline: str,
        status: Literal["completed", "failed"] = "completed",
    ) -> SessionTurn:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE session_turns
                SET seq_hi = (
                        SELECT MAX(seq) FROM session_events
                        WHERE turn_id = ? AND employee_id = ? AND session_id = ?
                    ),
                    headline = ?, status = ?, completed_at = ?
                WHERE id = ? AND employee_id = ? AND session_id = ? AND status = 'running'
                """,
                (
                    str(turn_id),
                    str(actor.employee_id),
                    str(session_id),
                    headline,
                    status,
                    timestamp,
                    str(turn_id),
                    str(actor.employee_id),
                    str(session_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ApplicationError(
                    "turn_not_running", "Session turn is not running.", status_code=409
                )
        return await self.get_turn(actor, session_id, turn_id)

    async def get_turn(self, actor: ActorContext, session_id: UUID, turn_id: UUID) -> SessionTurn:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT * FROM session_turns
                    WHERE id = ? AND employee_id = ? AND session_id = ?
                    """,
                    (str(turn_id), str(actor.employee_id), str(session_id)),
                )
            ).fetchone()
            if row is None:
                raise ApplicationError(
                    "turn_not_found", "Session turn was not found.", status_code=404
                )
            events = await _events(connection, actor.employee_id, session_id, turn_id)
        return _turn(row, events)

    async def list_turns(
        self, actor: ActorContext, session_id: UUID, *, include_running: bool = True
    ) -> list[SessionTurn]:
        status_clause = "" if include_running else "AND status != 'running'"
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    f"""
                    SELECT * FROM session_turns
                    WHERE employee_id = ? AND session_id = ? {status_clause}
                    ORDER BY seq_lo
                    """,
                    (str(actor.employee_id), str(session_id)),
                )
            ).fetchall()
            result = []
            for row in rows:
                events = await _events(
                    connection, actor.employee_id, session_id, UUID(str(row["id"]))
                )
                result.append(_turn(row, events))
        return result


async def _claim_seq(
    connection: aiosqlite.Connection, employee_id: UUID, session_id: UUID, timestamp: str
) -> int:
    row = await (
        await connection.execute(
            "SELECT next_seq FROM agent_sessions WHERE id = ? AND employee_id = ?",
            (str(session_id), str(employee_id)),
        )
    ).fetchone()
    if row is None:
        raise ApplicationError(
            "session_not_found", "Employee session was not found.", status_code=404
        )
    seq = int(row["next_seq"])
    await connection.execute(
        """
        UPDATE agent_sessions SET next_seq = next_seq + 1, updated_at = ?
        WHERE id = ? AND employee_id = ?
        """,
        (timestamp, str(session_id), str(employee_id)),
    )
    return seq


async def _require_running_turn(
    connection: aiosqlite.Connection, employee_id: UUID, session_id: UUID, turn_id: UUID
) -> None:
    row = await (
        await connection.execute(
            """
            SELECT 1 FROM session_turns
            WHERE id = ? AND employee_id = ? AND session_id = ? AND status = 'running'
            """,
            (str(turn_id), str(employee_id), str(session_id)),
        )
    ).fetchone()
    if row is None:
        raise ApplicationError("turn_not_running", "Session turn is not running.", status_code=409)


async def _insert_event(
    connection: aiosqlite.Connection,
    *,
    event_id: UUID,
    turn_id: UUID,
    session_id: UUID,
    employee_id: UUID,
    seq: int,
    event_type: str,
    text: str | None,
    tool_call_id: str | None,
    payload: dict[str, Any] | None,
    timestamp: str,
) -> None:
    await connection.execute(
        """
        INSERT INTO session_events(
            id, turn_id, session_id, employee_id, seq, event_type,
            text, tool_call_id, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(event_id),
            str(turn_id),
            str(session_id),
            str(employee_id),
            seq,
            event_type,
            text,
            tool_call_id,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")) if payload else None,
            timestamp,
        ),
    )


async def _events(
    connection: aiosqlite.Connection, employee_id: UUID, session_id: UUID, turn_id: UUID
) -> tuple[SessionEvent, ...]:
    rows = await (
        await connection.execute(
            """
            SELECT * FROM session_events
            WHERE turn_id = ? AND employee_id = ? AND session_id = ? ORDER BY seq
            """,
            (str(turn_id), str(employee_id), str(session_id)),
        )
    ).fetchall()
    return tuple(_event(row) for row in rows)


def _event(row: aiosqlite.Row) -> SessionEvent:
    return SessionEvent(
        event_id=UUID(str(row["id"])),
        turn_id=UUID(str(row["turn_id"])),
        session_id=UUID(str(row["session_id"])),
        employee_id=UUID(str(row["employee_id"])),
        seq=int(row["seq"]),
        event_type=str(row["event_type"]),
        text=str(row["text"]) if row["text"] is not None else None,
        tool_call_id=str(row["tool_call_id"]) if row["tool_call_id"] is not None else None,
        payload=json.loads(str(row["payload_json"])) if row["payload_json"] else None,
        created_at=str(row["created_at"]),
    )


def _turn(row: aiosqlite.Row, events: tuple[SessionEvent, ...]) -> SessionTurn:
    return SessionTurn(
        turn_id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        employee_id=UUID(str(row["employee_id"])),
        kind=cast(TurnKind, str(row["kind"])),
        seq_lo=int(row["seq_lo"]),
        seq_hi=int(row["seq_hi"]) if row["seq_hi"] is not None else None,
        headline=str(row["headline"]) if row["headline"] is not None else None,
        status=cast(Literal["running", "completed", "failed"], str(row["status"])),
        pending_action_id=UUID(str(row["pending_action_id"])) if row["pending_action_id"] else None,
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        events=events,
    )
