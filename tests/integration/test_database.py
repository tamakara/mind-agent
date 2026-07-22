import asyncio
from pathlib import Path

import aiosqlite
import pytest

from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.storage import Database
from workhub.storage import migrations as migration_module
from workhub.storage.migrations import MIGRATIONS, Migration

pytestmark = pytest.mark.integration

REQUIRED_TABLES = {
    "admin_sessions",
    "admin_users",
    "agent_sessions",
    "audit_events",
    "channel_identities",
    "employees",
    "feishu_settings",
    "knowledge_document_versions",
    "knowledge_generations",
    "knowledge_index_jobs",
    "knowledge_nodes",
    "mcp_clients",
    "mcp_tool_settings",
    "mcp_tools",
    "model_settings",
    "pending_actions",
    "processed_channel_events",
    "schema_migrations",
    "session_events",
    "session_turns",
}


async def _insert_employee(connection: aiosqlite.Connection, employee_id: str, number: str) -> None:
    timestamp = format_rfc3339(utc_now())
    await connection.execute(
        """
        INSERT INTO employees(
            id, employee_no, display_name, department, timezone, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (employee_id, number, number, "Engineering", "Asia/Shanghai", timestamp, timestamp),
    )


async def test_migrations_are_idempotent_and_connections_have_required_pragmas(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db", busy_timeout_ms=2_500)

    await database.migrate()
    await database.migrate()

    async with database.connect() as connection:
        table_cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        tables = {str(row["name"]) for row in await table_cursor.fetchall()}
        journal_mode = await (await connection.execute("PRAGMA journal_mode")).fetchone()
        foreign_keys = await (await connection.execute("PRAGMA foreign_keys")).fetchone()
        busy_timeout = await (await connection.execute("PRAGMA busy_timeout")).fetchone()
        migrations = await (
                await connection.execute(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                )
        ).fetchall()

    assert tables >= REQUIRED_TABLES
    assert journal_mode[0] == "wal"
    assert foreign_keys[0] == 1
    assert busy_timeout[0] == 2_500
    assert [(row["version"], row["name"]) for row in migrations] == [
        (migration.version, migration.name) for migration in MIGRATIONS
    ]


async def test_failed_transaction_rolls_back_all_writes(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()

    with pytest.raises(RuntimeError, match="abort"):
        async with database.transaction(write=True) as connection:
            await _insert_employee(connection, str(new_uuid4()), "E10001")
            raise RuntimeError("abort")

    async with database.connect() as connection:
        row = await (await connection.execute("SELECT COUNT(*) FROM employees")).fetchone()
    assert row[0] == 0


async def test_composite_constraints_prevent_cross_employee_scroll_access(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    employee_a, employee_b = str(new_uuid4()), str(new_uuid4())
    session_a, session_b, turn_a = str(new_uuid4()), str(new_uuid4()), str(new_uuid4())
    timestamp = format_rfc3339(utc_now())

    async with database.transaction(write=True) as connection:
        await _insert_employee(connection, employee_a, "E10001")
        await _insert_employee(connection, employee_b, "E10002")
        await connection.executemany(
            """
            INSERT INTO agent_sessions(id, employee_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                (session_a, employee_a, timestamp, timestamp),
                (session_b, employee_b, timestamp, timestamp),
            ),
        )
        await connection.execute(
            """
            INSERT INTO session_turns(
                id, session_id, employee_id, kind, seq_lo, status, created_at
            ) VALUES (?, ?, ?, 'normal', 1, 'running', ?)
            """,
            (turn_a, session_a, employee_a, timestamp),
        )

    with pytest.raises(aiosqlite.IntegrityError):
        async with database.transaction(write=True) as connection:
            await connection.execute(
                """
                INSERT INTO session_events(
                    id, turn_id, session_id, employee_id, seq, event_type, created_at
                ) VALUES (?, ?, ?, ?, 1, 'user_message', ?)
                """,
                (str(new_uuid4()), turn_a, session_b, employee_b, timestamp),
            )


async def test_failed_migration_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    failing = Migration(
        version=max(migration.version for migration in MIGRATIONS) + 1,
        name="failing_test_migration",
        statements=("CREATE TABLE must_rollback(id TEXT PRIMARY KEY)", "INVALID SQL"),
    )
    monkeypatch.setattr(migration_module, "MIGRATIONS", (*MIGRATIONS, failing))

    with pytest.raises(aiosqlite.OperationalError):
        await database.migrate()

    async with database.connect() as connection:
        table = await (
            await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'must_rollback'"
            )
        ).fetchone()
        version = await (
            await connection.execute(
                "SELECT version FROM schema_migrations WHERE version = ?", (failing.version,)
            )
        ).fetchone()
    assert table is None
    assert version is None


async def test_concurrent_short_writes_are_serialized(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db", busy_timeout_ms=5_000)
    await database.migrate()

    async def insert(number: str) -> None:
        async with database.transaction(write=True) as connection:
            await _insert_employee(connection, str(new_uuid4()), number)
            await asyncio.sleep(0.02)

    await asyncio.gather(insert("E10001"), insert("E10002"))

    async with database.connect() as connection:
        row = await (await connection.execute("SELECT COUNT(*) FROM employees")).fetchone()
    assert row[0] == 2
