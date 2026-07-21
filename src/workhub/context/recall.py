import asyncio
from typing import Any, Literal
from uuid import UUID

import aiosqlite

from workhub.domain import ActorContext
from workhub.storage import Database


class RecallService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._fts_lock = asyncio.Lock()

    async def expand(
        self,
        actor: ActorContext,
        session_id: UUID,
        *,
        seq_start: int,
        seq_end: int,
    ) -> list[dict[str, Any]]:
        if seq_start < 1 or seq_end < seq_start:
            raise ValueError("Invalid seq range")
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT t.id AS turn_id, t.headline, e.seq, e.event_type, e.text,
                           e.tool_call_id, e.payload_json
                    FROM session_events e
                    JOIN session_turns t
                      ON t.id = e.turn_id
                     AND t.employee_id = e.employee_id
                     AND t.session_id = e.session_id
                    WHERE e.employee_id = ? AND e.session_id = ?
                      AND e.seq BETWEEN ? AND ?
                    ORDER BY e.seq
                    """,
                    (str(actor.employee_id), str(session_id), seq_start, seq_end),
                )
            ).fetchall()
        return _group(list(rows))

    async def search(
        self, actor: ActorContext, session_id: UUID, *, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        async with self._fts_lock, self.database.connect() as connection:
            try:
                await self._prepare_fts(connection)
                rows = await self._search_fts(connection, actor, session_id, query, limit)
            except aiosqlite.OperationalError:
                rows = await self._search_like(connection, actor, session_id, query, limit)
        return _group(rows)

    async def _prepare_fts(self, connection: aiosqlite.Connection) -> None:
        await connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS session_events_fts USING fts5(
                event_id UNINDEXED,
                employee_id UNINDEXED,
                session_id UNINDEXED,
                text,
                headline
            )
            """
        )
        await connection.execute("DELETE FROM session_events_fts")
        await connection.execute(
            """
            INSERT INTO session_events_fts(event_id, employee_id, session_id, text, headline)
            SELECT e.id, e.employee_id, e.session_id, COALESCE(e.text, ''),
                   COALESCE(t.headline, '')
            FROM session_events e
            JOIN session_turns t
              ON t.id = e.turn_id
             AND t.employee_id = e.employee_id
             AND t.session_id = e.session_id
            """
        )

    async def execute(
        self,
        actor: ActorContext,
        session_id: UUID,
        *,
        mode: Literal["expand", "search"],
        seq_start: int | None = None,
        seq_end: int | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        if mode == "expand" and seq_start is not None and seq_end is not None:
            return await self.expand(actor, session_id, seq_start=seq_start, seq_end=seq_end)
        if mode == "search" and query is not None:
            return await self.search(actor, session_id, query=query)
        raise ValueError("Arguments do not match recall mode")

    async def _search_fts(
        self,
        connection: aiosqlite.Connection,
        actor: ActorContext,
        session_id: UUID,
        query: str,
        limit: int,
    ) -> list[aiosqlite.Row]:
        return list(
            await (
                await connection.execute(
                    """
                SELECT t.id AS turn_id, t.headline, e.seq, e.event_type, e.text,
                       e.tool_call_id, e.payload_json
                FROM session_events_fts
                JOIN session_events e ON e.id = session_events_fts.event_id
                JOIN session_turns t ON t.id = e.turn_id
                WHERE session_events_fts MATCH ?
                  AND e.employee_id = ? AND e.session_id = ?
                ORDER BY bm25(session_events_fts), e.seq LIMIT ?
                """,
                    (query, str(actor.employee_id), str(session_id), limit),
                )
            ).fetchall()
        )

    async def _search_like(
        self,
        connection: aiosqlite.Connection,
        actor: ActorContext,
        session_id: UUID,
        query: str,
        limit: int,
    ) -> list[aiosqlite.Row]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        return list(
            await (
                await connection.execute(
                    """
                SELECT t.id AS turn_id, t.headline, e.seq, e.event_type, e.text,
                       e.tool_call_id, e.payload_json
                FROM session_events e
                JOIN session_turns t
                  ON t.id = e.turn_id
                 AND t.employee_id = e.employee_id
                 AND t.session_id = e.session_id
                WHERE e.employee_id = ? AND e.session_id = ?
                  AND (e.text LIKE ? ESCAPE '\\' OR t.headline LIKE ? ESCAPE '\\')
                ORDER BY e.seq DESC LIMIT ?
                """,
                    (str(actor.employee_id), str(session_id), pattern, pattern, limit),
                )
            ).fetchall()
        )


def _group(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        turn_id = str(row["turn_id"])
        group = groups.setdefault(
            turn_id,
            {"turn_id": turn_id, "headline": row["headline"], "events": []},
        )
        group["events"].append(
            {
                "seq": int(row["seq"]),
                "event_type": str(row["event_type"]),
                "text": row["text"],
                "tool_call_id": row["tool_call_id"],
                "payload_json": row["payload_json"],
            }
        )
    return list(groups.values())
