from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        finally:
            await connection.close()

    @asynccontextmanager
    async def transaction(self, *, write: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    async def migrate(self) -> None:
        async with self.transaction(write=True) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = await (
                await connection.execute("SELECT 1 FROM schema_migrations WHERE version = 1")
            ).fetchone()
            if row is not None:
                return
            for statement in INITIAL_SCHEMA:
                await connection.execute(statement)
            await connection.execute(
                """
                INSERT INTO schema_migrations(version, name, applied_at)
                VALUES (1, 'initial_oa_schema', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )


INITIAL_SCHEMA = (
    """
    CREATE TABLE employees (
        employee_id TEXT PRIMARY KEY,
        employee_no TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE leave_balances (
        employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
        year INTEGER NOT NULL CHECK (year >= 2000 AND year <= 9999),
        entitled_days REAL NOT NULL CHECK (entitled_days >= 0),
        used_days REAL NOT NULL DEFAULT 0 CHECK (used_days >= 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (employee_id, year),
        CHECK (used_days <= entitled_days)
    )
    """,
    """
    CREATE TABLE leave_requests (
        request_id TEXT PRIMARY KEY,
        employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE RESTRICT,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        leave_type TEXT NOT NULL CHECK (leave_type = 'annual_leave'),
        reason TEXT,
        working_days REAL NOT NULL CHECK (working_days > 0),
        status TEXT NOT NULL CHECK (
            status IN ('pending_approval', 'approved', 'rejected')
        ),
        submitted_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (start_date <= end_date)
    )
    """,
    """
    CREATE TABLE idempotency_records (
        employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE RESTRICT,
        idempotency_key TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE
            REFERENCES leave_requests(request_id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (employee_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX idx_leave_requests_employee_dates
    ON leave_requests(employee_id, start_date, end_date, status)
    """,
    """
    CREATE INDEX idx_leave_requests_status
    ON leave_requests(status, updated_at)
    """,
)
