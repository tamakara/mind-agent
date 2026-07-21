from typing import Any

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.errors import ApplicationError
from workhub.settings.models import FeishuSetting, RuntimeSetting, StoredFeishuSetting
from workhub.storage import Database


class RuntimeSettingsRepository:
    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def get(self) -> RuntimeSetting:
        async with self.database.connect() as connection:
            row = await _runtime_row(connection)
        return _runtime_public(row) if row is not None else RuntimeSetting()

    async def upsert(
        self,
        setting: RuntimeSetting,
        *,
        expected_revision: int | None,
        actor_id: str,
        request_id: str,
    ) -> RuntimeSetting:
        now = format_rfc3339(utc_now())
        values = _runtime_values(setting)
        async with self.database.transaction(write=True) as connection:
            existing = await _runtime_row(connection)
            if existing is None:
                if expected_revision not in (None, 0):
                    raise _revision_conflict()
                await connection.execute(
                    """INSERT INTO runtime_settings(
                        id, provider_test_timeout_seconds, agent_timeout_seconds,
                        agent_tool_timeout_seconds, agent_max_iterations,
                        agent_context_token_budget, mcp_timeout_seconds,
                        pending_action_ttl_seconds, feishu_reconnect_attempts,
                        feishu_reconnect_delay_seconds, feishu_api_timeout_seconds,
                        revision, created_at, updated_at
                    ) VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (*values, now, now),
                )
            else:
                if expected_revision != int(existing["revision"]):
                    raise _revision_conflict()
                await connection.execute(
                    """UPDATE runtime_settings SET
                        provider_test_timeout_seconds = ?, agent_timeout_seconds = ?,
                        agent_tool_timeout_seconds = ?, agent_max_iterations = ?,
                        agent_context_token_budget = ?, mcp_timeout_seconds = ?,
                        pending_action_ttl_seconds = ?, feishu_reconnect_attempts = ?,
                        feishu_reconnect_delay_seconds = ?, feishu_api_timeout_seconds = ?,
                        revision = revision + 1, updated_at = ? WHERE id = 'default'""",
                    (*values, now),
                )
            row = await _runtime_row(connection)
            assert row is not None
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="runtime_settings.configured",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="runtime_settings",
                    subject_id="default",
                    request_id=request_id,
                    summary={"revision": int(row["revision"])},
                ),
            )
        return _runtime_public(row)


class FeishuSettingsRepository:
    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def get(self) -> StoredFeishuSetting | None:
        async with self.database.connect() as connection:
            row = await _feishu_row(connection)
        return _stored_feishu(row) if row is not None else None

    async def upsert(
        self,
        *,
        app_id: str,
        app_secret: str | None,
        expected_revision: int | None,
        actor_id: str,
        request_id: str,
    ) -> StoredFeishuSetting:
        now = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            existing = await _feishu_row(connection)
            if existing is None:
                if expected_revision is not None:
                    raise _revision_conflict()
                if not app_secret:
                    raise ApplicationError("app_secret_required", "App secret is required.")
                await connection.execute(
                    """INSERT INTO feishu_settings(
                        id, app_id, app_secret, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)""",
                    (str(new_uuid4()), app_id, app_secret, now, now),
                )
            else:
                if expected_revision != int(existing["revision"]):
                    raise _revision_conflict()
                secret = app_secret or str(existing["app_secret"])
                await connection.execute(
                    """UPDATE feishu_settings SET app_id = ?, app_secret = ?,
                       revision = revision + 1, updated_at = ? WHERE id = ?""",
                    (app_id, secret, now, existing["id"]),
                )
            row = await _feishu_row(connection)
            assert row is not None
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="feishu.configured",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="feishu_setting",
                    subject_id=str(row["id"]),
                    request_id=request_id,
                    summary={"app_id": app_id, "revision": int(row["revision"])},
                ),
            )
        return _stored_feishu(row)

    async def delete(
        self, *, expected_revision: int, actor_id: str, request_id: str
    ) -> None:
        async with self.database.transaction(write=True) as connection:
            row = await _feishu_row(connection)
            if row is None:
                raise ApplicationError(
                    "feishu_not_configured", "Feishu is not configured.", status_code=404
                )
            if expected_revision != int(row["revision"]):
                raise _revision_conflict()
            await connection.execute("DELETE FROM feishu_settings WHERE id = ?", (row["id"],))
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="feishu.deleted",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="feishu_setting",
                    subject_id=str(row["id"]),
                    request_id=request_id,
                    summary={"app_id": str(row["app_id"])},
                ),
            )


async def _runtime_row(connection: aiosqlite.Connection) -> aiosqlite.Row | None:
    return await (
        await connection.execute("SELECT * FROM runtime_settings WHERE id = 'default'")
    ).fetchone()


def _runtime_values(setting: RuntimeSetting) -> tuple[Any, ...]:
    return (
        setting.provider_test_timeout_seconds,
        setting.agent_timeout_seconds,
        setting.agent_tool_timeout_seconds,
        setting.agent_max_iterations,
        setting.agent_context_token_budget,
        setting.mcp_timeout_seconds,
        setting.pending_action_ttl_seconds,
        setting.feishu_reconnect_attempts,
        setting.feishu_reconnect_delay_seconds,
        setting.feishu_api_timeout_seconds,
    )


def _runtime_public(row: aiosqlite.Row) -> RuntimeSetting:
    return RuntimeSetting(**{key: row[key] for key in row.keys() if key != "id"})  # noqa: SIM118


async def _feishu_row(connection: aiosqlite.Connection) -> aiosqlite.Row | None:
    return await (
        await connection.execute("SELECT * FROM feishu_settings ORDER BY created_at LIMIT 1")
    ).fetchone()


def _stored_feishu(row: aiosqlite.Row) -> StoredFeishuSetting:
    return StoredFeishuSetting(
        public=FeishuSetting(
            app_id=str(row["app_id"]),
            app_secret_configured=bool(row["app_secret"]),
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        ),
        app_secret=str(row["app_secret"]),
    )


def _revision_conflict() -> ApplicationError:
    return ApplicationError("revision_conflict", "Resource revision is stale.", status_code=409)
