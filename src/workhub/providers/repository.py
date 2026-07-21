from dataclasses import dataclass
from typing import Literal, cast

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.domain.providers import ModelSetting
from workhub.errors import ApplicationError
from workhub.storage import Database

ProviderKind = Literal["chat", "embedding"]


@dataclass(frozen=True, slots=True)
class StoredModelSetting:
    public: ModelSetting
    api_key: str


class ModelSettingsRepository:
    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def list(self) -> list[ModelSetting]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute("SELECT * FROM model_settings ORDER BY provider_kind")
            ).fetchall()
        return [_public(row) for row in rows]

    async def get(self, provider_kind: ProviderKind) -> StoredModelSetting:
        async with self.database.connect() as connection:
            row = await _row(connection, provider_kind)
        if row is None:
            raise ApplicationError(
                "provider_not_configured",
                f"{provider_kind.capitalize()} provider is not configured.",
                status_code=404,
            )
        return StoredModelSetting(public=_public(row), api_key=str(row["api_key"]))

    async def upsert(
        self,
        provider_kind: ProviderKind,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        expected_revision: int | None,
        actor_id: str,
        request_id: str,
    ) -> ModelSetting:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            existing = await _row(connection, provider_kind)
            if existing is None:
                if expected_revision is not None:
                    raise ApplicationError(
                        "revision_conflict", "Resource revision is stale.", status_code=409
                    )
                if not api_key:
                    raise ApplicationError("api_key_required", "API key is required.")
                await connection.execute(
                    """
                    INSERT INTO model_settings(
                        id, provider_kind, base_url, api_key, model, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        str(new_uuid4()),
                        provider_kind,
                        base_url,
                        api_key,
                        model,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                if expected_revision != int(existing["revision"]):
                    raise ApplicationError(
                        "revision_conflict", "Resource revision is stale.", status_code=409
                    )
                next_key = api_key if api_key else str(existing["api_key"])
                await connection.execute(
                    """
                    UPDATE model_settings
                    SET base_url = ?, api_key = ?, model = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE provider_kind = ?
                    """,
                    (base_url, next_key, model, timestamp, provider_kind),
                )
            row = await _row(connection, provider_kind)
            assert row is not None
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="provider.configured",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="model_setting",
                    subject_id=provider_kind,
                    request_id=request_id,
                    summary={"provider_kind": provider_kind, "base_url": base_url, "model": model},
                ),
            )
        return _public(row)

    async def bootstrap(
        self, provider_kind: ProviderKind, *, base_url: str, api_key: str, model: str
    ) -> None:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                """
                INSERT INTO model_settings(
                    id, provider_kind, base_url, api_key, model, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(provider_kind) DO NOTHING
                """,
                (str(new_uuid4()), provider_kind, base_url, api_key, model, timestamp, timestamp),
            )

    async def delete(
        self,
        provider_kind: ProviderKind,
        *,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> None:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                DELETE FROM model_settings
                WHERE provider_kind = ? AND revision = ?
                """,
                (provider_kind, expected_revision),
            )
            if cursor.rowcount != 1:
                existing = await _row(connection, provider_kind)
                if existing is None:
                    raise ApplicationError(
                        "provider_not_configured",
                        f"{provider_kind.capitalize()} provider is not configured.",
                        status_code=404,
                    )
                raise ApplicationError(
                    "revision_conflict", "Resource revision is stale.", status_code=409
                )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="provider.deleted",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="model_setting",
                    subject_id=provider_kind,
                    request_id=request_id,
                    summary={"provider_kind": provider_kind},
                ),
            )


async def _row(
    connection: aiosqlite.Connection, provider_kind: ProviderKind
) -> aiosqlite.Row | None:
    return await (
        await connection.execute(
            "SELECT * FROM model_settings WHERE provider_kind = ?", (provider_kind,)
        )
    ).fetchone()


def _public(row: aiosqlite.Row) -> ModelSetting:
    return ModelSetting(
        provider_kind=cast(ProviderKind, str(row["provider_kind"])),
        base_url=str(row["base_url"]),
        model=str(row["model"]),
        api_key_configured=bool(row["api_key"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
