import json
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import aiosqlite
from pydantic import AnyHttpUrl

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import (
    McpClient,
    McpToolEffect,
    ToolDescriptor,
    format_rfc3339,
    new_uuid4,
    utc_now,
)
from workhub.domain.tools import strip_trusted_subject_fields
from workhub.errors import ApplicationError
from workhub.storage import Database

CLIENT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SAFE_PART = re.compile(r"[^a-zA-Z0-9_]+")


@dataclass(frozen=True, slots=True)
class StoredMcpClient:
    public: McpClient
    headers: dict[str, str]


class McpRepository:
    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def list_clients(self) -> list[McpClient]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute("SELECT * FROM mcp_clients ORDER BY name")
            ).fetchall()
        return [_client(row) for row in rows]

    async def enabled_clients(self) -> list[StoredMcpClient]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM mcp_clients WHERE enabled = 1 ORDER BY client_key"
                )
            ).fetchall()
        return [_stored_client(row) for row in rows]

    async def get_client(self, client_id: UUID) -> StoredMcpClient:
        async with self.database.connect() as connection:
            row = await _client_row(connection, client_id)
        if row is None:
            raise ApplicationError(
                "mcp_client_not_found", "MCP client was not found.", status_code=404
            )
        return _stored_client(row)

    async def get_client_by_key(self, client_key: str) -> StoredMcpClient:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM mcp_clients WHERE client_key = ?", (client_key,)
                )
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "mcp_client_not_found", "MCP client was not found.", status_code=404
            )
        return _stored_client(row)

    async def upsert_client(
        self,
        *,
        client_id: UUID | None,
        client_key: str,
        name: str,
        url: str,
        headers: dict[str, str] | None,
        enabled: bool,
        expected_revision: int | None,
        actor_id: str,
        request_id: str,
    ) -> McpClient:
        if not CLIENT_KEY.fullmatch(client_key):
            raise ApplicationError(
                "invalid_client_key",
                "Client key must use lowercase letters, digits, and underscores.",
            )
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            existing = await _client_row(connection, client_id) if client_id else None
            try:
                if existing is None:
                    if client_id is not None or expected_revision is not None:
                        raise ApplicationError(
                            "revision_conflict", "Resource revision is stale.", status_code=409
                        )
                    identifier = new_uuid4()
                    await connection.execute(
                        """
                        INSERT INTO mcp_clients(id, client_key, name, url, headers_json, enabled,
                                                revision, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            str(identifier),
                            client_key,
                            name,
                            url,
                            _headers_json(headers or {}),
                            int(enabled),
                            timestamp,
                            timestamp,
                        ),
                    )
                else:
                    if expected_revision != int(existing["revision"]):
                        raise ApplicationError(
                            "revision_conflict", "Resource revision is stale.", status_code=409
                        )
                    identifier = UUID(str(existing["id"]))
                    next_headers = (
                        _headers_json(headers) if headers is not None else existing["headers_json"]
                    )
                    await connection.execute(
                        """
                        UPDATE mcp_clients SET client_key = ?, name = ?, url = ?, headers_json = ?,
                            enabled = ?, revision = revision + 1, updated_at = ? WHERE id = ?
                        """,
                        (
                            client_key,
                            name,
                            url,
                            next_headers,
                            int(enabled),
                            timestamp,
                            str(identifier),
                        ),
                    )
            except aiosqlite.IntegrityError as exc:
                raise ApplicationError(
                    "mcp_client_conflict", "MCP client key already exists.", status_code=409
                ) from exc
            row = await _client_row(connection, identifier)
            assert row is not None
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="mcp.client_configured",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="mcp_client",
                    subject_id=str(identifier),
                    request_id=request_id,
                    summary={
                        "client_key": client_key,
                        "url": url,
                        "enabled": enabled,
                        "headers_configured": bool(row["headers_json"]),
                    },
                ),
            )
        return _client(row)

    async def delete_client(
        self, client_id: UUID, *, expected_revision: int, actor_id: str, request_id: str
    ) -> None:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                "DELETE FROM mcp_clients WHERE id = ? AND revision = ?",
                (str(client_id), expected_revision),
            )
            if cursor.rowcount != 1:
                row = await _client_row(connection, client_id)
                code = "mcp_client_not_found" if row is None else "revision_conflict"
                raise ApplicationError(
                    code,
                    "MCP client was not found." if row is None else "Resource revision is stale.",
                    status_code=404 if row is None else 409,
                )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="mcp.client_deleted",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="mcp_client",
                    subject_id=str(client_id),
                    request_id=request_id,
                    summary={},
                ),
            )

    async def sync_tools(
        self, client: StoredMcpClient, tools: list[dict[str, Any]]
    ) -> list[ToolDescriptor]:
        timestamp = format_rfc3339(utc_now())
        seen: set[str] = set()
        async with self.database.transaction(write=True) as connection:
            for discovered in tools:
                original_name = str(discovered["name"])
                model_name = model_tool_name(client.public.client_key, original_name)
                if model_name in seen:
                    raise ApplicationError(
                        "mcp_tool_name_conflict",
                        "Discovered tools produce conflicting safe model names.",
                        status_code=409,
                    )
                seen.add(model_name)
                schema = discovered.get("input_schema")
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                existing = await (
                    await connection.execute(
                        "SELECT id FROM mcp_tools WHERE client_id = ? AND original_name = ?",
                        (str(client.public.client_id), original_name),
                    )
                ).fetchone()
                tool_id = UUID(str(existing["id"])) if existing else new_uuid4()
                try:
                    await connection.execute(
                        """
                        INSERT INTO mcp_tools(id, client_id, original_name, model_name, description,
                                              input_schema_json, discovered_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(client_id, original_name) DO UPDATE SET
                            model_name = excluded.model_name, description = excluded.description,
                            input_schema_json = excluded.input_schema_json,
                            discovered_at = excluded.discovered_at
                        """,
                        (
                            str(tool_id),
                            str(client.public.client_id),
                            original_name,
                            model_name,
                            discovered.get("description"),
                            json.dumps(
                                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                            ),
                            timestamp,
                        ),
                    )
                except aiosqlite.IntegrityError as exc:
                    raise ApplicationError(
                        "mcp_tool_name_conflict",
                        "Tool model name conflicts with another client.",
                        status_code=409,
                    ) from exc
                await connection.execute(
                    """
                    INSERT INTO mcp_tool_settings(
                        tool_id, allowlisted, policy, revision, updated_at
                    )
                    VALUES (?, 0, 'deny', 0, ?) ON CONFLICT(tool_id) DO NOTHING
                    """,
                    (str(tool_id), timestamp),
                )
        return await self.list_tools(client_id=client.public.client_id)

    async def list_tools(self, *, client_id: UUID | None = None) -> list[ToolDescriptor]:
        query = _TOOL_SELECT
        params: tuple[object, ...] = ()
        if client_id is not None:
            query += " WHERE t.client_id = ?"
            params = (str(client_id),)
        query += " ORDER BY c.client_key, t.original_name"
        async with self.database.connect() as connection:
            rows = await (await connection.execute(query, params)).fetchall()
        return [_tool(row) for row in rows]

    async def get_tool(self, model_name: str) -> ToolDescriptor | None:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(_TOOL_SELECT + " WHERE t.model_name = ?", (model_name,))
            ).fetchone()
        return _tool(row) if row else None

    async def update_tool_setting(
        self,
        tool_id: UUID,
        *,
        allowlisted: bool,
        effect: McpToolEffect,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ToolDescriptor:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE mcp_tool_settings
                SET allowlisted = ?, policy = ?, revision = revision + 1, updated_at = ?
                WHERE tool_id = ? AND revision = ?
                """,
                (int(allowlisted), effect, timestamp, str(tool_id), expected_revision),
            )
            if cursor.rowcount != 1:
                row = await (
                    await connection.execute(
                        "SELECT 1 FROM mcp_tools WHERE id = ?", (str(tool_id),)
                    )
                ).fetchone()
                raise ApplicationError(
                    "mcp_tool_not_found" if row is None else "revision_conflict",
                    "MCP tool was not found." if row is None else "Resource revision is stale.",
                    status_code=404 if row is None else 409,
                )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="mcp.tool_policy_changed",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="mcp_tool",
                    subject_id=str(tool_id),
                    request_id=request_id,
                    summary={"allowlisted": allowlisted, "effect": effect},
                ),
            )
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(_TOOL_SELECT + " WHERE t.id = ?", (str(tool_id),))
            ).fetchone()
        assert row is not None
        return _tool(row)


_TOOL_SELECT = """
SELECT t.*, c.client_key, s.allowlisted, s.policy, s.revision AS setting_revision
FROM mcp_tools t JOIN mcp_clients c ON c.id = t.client_id
JOIN mcp_tool_settings s ON s.tool_id = t.id
"""


def model_tool_name(client_key: str, original_name: str) -> str:
    part = SAFE_PART.sub("_", original_name).strip("_").lower()
    if not part:
        raise ApplicationError(
            "invalid_mcp_tool_name", "MCP tool name cannot be represented safely."
        )
    return f"mcp__{client_key}__{part}"[:128]


async def _client_row(
    connection: aiosqlite.Connection, client_id: UUID | None
) -> aiosqlite.Row | None:
    if client_id is None:
        return None
    return await (
        await connection.execute("SELECT * FROM mcp_clients WHERE id = ?", (str(client_id),))
    ).fetchone()


def _headers_json(headers: dict[str, str]) -> str | None:
    if any(
        not isinstance(k, str) or not isinstance(v, str) or not k.strip()
        for k, v in headers.items()
    ):
        raise ApplicationError(
            "invalid_mcp_headers", "MCP headers must contain string names and values."
        )
    return json.dumps(headers, sort_keys=True, separators=(",", ":")) if headers else None


def _client(row: aiosqlite.Row) -> McpClient:
    return McpClient(
        client_id=UUID(str(row["id"])),
        client_key=str(row["client_key"]),
        name=str(row["name"]),
        url=AnyHttpUrl(str(row["url"])),
        headers_configured=bool(row["headers_json"]),
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stored_client(row: aiosqlite.Row) -> StoredMcpClient:
    raw = json.loads(str(row["headers_json"])) if row["headers_json"] else {}
    return StoredMcpClient(public=_client(row), headers={str(k): str(v) for k, v in raw.items()})


def _tool(row: aiosqlite.Row) -> ToolDescriptor:
    schema = cast(dict[str, Any], json.loads(str(row["input_schema_json"])))
    return ToolDescriptor(
        tool_id=UUID(str(row["id"])),
        client_id=UUID(str(row["client_id"])),
        client_key=str(row["client_key"]),
        original_name=str(row["original_name"]),
        model_name=str(row["model_name"]),
        description=str(row["description"]) if row["description"] is not None else None,
        input_schema=strip_trusted_subject_fields(schema),
        allowlisted=bool(row["allowlisted"]),
        effect=cast(McpToolEffect, str(row["policy"])),
        revision=int(row["setting_revision"]),
        discovered_at=str(row["discovered_at"]),
    )
