from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import (
    ActorContext,
    ChannelIdentity,
    Employee,
    IdentityResolution,
    Page,
    format_rfc3339,
    new_uuid4,
    parse_rfc3339,
    utc_now,
)
from workhub.errors import ApplicationError
from workhub.storage import Database


class ResourceNotFoundError(ApplicationError):
    def __init__(self, resource: str) -> None:
        super().__init__("not_found", f"{resource} was not found.", status_code=404)


class RevisionConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            "revision_conflict", "The resource was modified by another request.", status_code=409
        )


class DirectoryConstraintError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__("directory_constraint", message, status_code=409)


class EmployeeRepository:
    UPDATE_FIELDS = frozenset(
        {"employee_no", "display_name", "department", "manager_employee_id", "timezone", "status"}
    )

    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def create(
        self,
        *,
        employee_no: str,
        display_name: str,
        department: str,
        manager_employee_id: UUID | None,
        timezone: str,
        actor_id: str,
        request_id: str,
    ) -> Employee:
        _validate_employee_fields(
            employee_no=employee_no,
            display_name=display_name,
            department=department,
            timezone=timezone,
        )
        employee_id = str(new_uuid4())
        timestamp = format_rfc3339(utc_now())
        try:
            async with self.database.transaction(write=True) as connection:
                await connection.execute(
                    """
                    INSERT INTO employees(
                        id, employee_no, display_name, department, manager_employee_id,
                        timezone, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        employee_id,
                        employee_no,
                        display_name,
                        department,
                        str(manager_employee_id) if manager_employee_id else None,
                        timezone,
                        timestamp,
                        timestamp,
                    ),
                )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="employee.created",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="employee",
                        subject_id=employee_id,
                        request_id=request_id,
                        summary={"employee_no": employee_no, "status": "active"},
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            raise DirectoryConstraintError(
                "Employee number or manager reference is invalid."
            ) from exc
        return await self.get(UUID(employee_id))

    async def get(self, employee_id: UUID) -> Employee:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM employees WHERE id = ?", (str(employee_id),)
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Employee")
        return _employee_from_row(row)

    async def list(self, *, limit: int, offset: int) -> Page[Employee]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM employees ORDER BY employee_no LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ).fetchall()
            total_row = await (
                await connection.execute("SELECT COUNT(*) FROM employees")
            ).fetchone()
        assert total_row is not None
        return Page(
            items=[_employee_from_row(row) for row in rows],
            total=int(total_row[0]),
            limit=limit,
            offset=offset,
        )

    async def update(
        self,
        employee_id: UUID,
        *,
        expected_revision: int,
        changes: dict[str, Any],
        actor_id: str,
        request_id: str,
    ) -> Employee:
        invalid = set(changes).difference(self.UPDATE_FIELDS)
        if invalid or not changes:
            raise ValueError("Employee update must contain supported fields")
        if changes.get("manager_employee_id") == employee_id:
            raise DirectoryConstraintError("An employee cannot manage themselves.")
        _validate_employee_fields(**changes)
        assignments: list[str] = []
        parameters: list[Any] = []
        for field, value in changes.items():
            assignments.append(f"{field} = ?")
            parameters.append(str(value) if isinstance(value, UUID) else value)
        assignments.extend(("revision = revision + 1", "updated_at = ?"))
        parameters.extend((format_rfc3339(utc_now()), str(employee_id), expected_revision))
        try:
            async with self.database.transaction(write=True) as connection:
                cursor = await connection.execute(
                    f"UPDATE employees SET {', '.join(assignments)} WHERE id = ? AND revision = ?",
                    parameters,
                )
                if cursor.rowcount != 1:
                    await _raise_missing_or_revision(connection, "employees", str(employee_id))
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="employee.updated",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="employee",
                        subject_id=str(employee_id),
                        request_id=request_id,
                        summary={"changed_fields": sorted(changes)},
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            raise DirectoryConstraintError(
                "Employee number or manager reference is invalid."
            ) from exc
        return await self.get(employee_id)


class IdentityRepository:
    def __init__(self, database: Database, audit: AuditWriter) -> None:
        self.database = database
        self.audit = audit

    async def upsert_unbound(
        self, *, app_id: str, platform_user_id: str, display_name: str | None
    ) -> ChannelIdentity:
        timestamp = format_rfc3339(utc_now())
        identity_id = str(new_uuid4())
        async with self.database.transaction(write=True) as connection:
            existing = await (
                await connection.execute(
                    """
                    SELECT id FROM channel_identities
                    WHERE channel = 'feishu' AND app_id = ? AND platform_user_id = ?
                    """,
                    (app_id, platform_user_id),
                )
            ).fetchone()
            await connection.execute(
                """
                INSERT INTO channel_identities(
                    id, channel, app_id, platform_user_id, display_name,
                    binding_status, created_at, updated_at
                ) VALUES (?, 'feishu', ?, ?, ?, 'unbound', ?, ?)
                ON CONFLICT(channel, app_id, platform_user_id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, channel_identities.display_name),
                    revision = channel_identities.revision + 1,
                    updated_at = excluded.updated_at
                """,
                (identity_id, app_id, platform_user_id, display_name, timestamp, timestamp),
            )
            row = await _identity_row(connection, app_id, platform_user_id)
            if existing is None:
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="identity.discovered",
                        subject_type="channel_identity",
                        subject_id=str(row["id"]),
                        summary={"channel": "feishu", "app_id": app_id},
                    ),
                )
        return _identity_from_row(row)

    async def list(
        self, *, binding_status: str | None, limit: int, offset: int
    ) -> Page[ChannelIdentity]:
        where = "WHERE binding_status = ?" if binding_status else ""
        parameters: tuple[Any, ...] = (
            (binding_status, limit, offset) if binding_status else (limit, offset)
        )
        count_parameters: tuple[Any, ...] = (binding_status,) if binding_status else ()
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    f"""
                    SELECT * FROM channel_identities {where}
                    ORDER BY created_at LIMIT ? OFFSET ?
                    """,
                    parameters,
                )
            ).fetchall()
            total = await (
                await connection.execute(
                    f"SELECT COUNT(*) FROM channel_identities {where}", count_parameters
                )
            ).fetchone()
        assert total is not None
        return Page(
            items=[_identity_from_row(row) for row in rows],
            total=int(total[0]),
            limit=limit,
            offset=offset,
        )

    async def bind(
        self,
        identity_id: UUID,
        employee_id: UUID,
        *,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> tuple[ChannelIdentity, UUID]:
        timestamp = format_rfc3339(utc_now())
        session_id = str(new_uuid4())
        try:
            async with self.database.transaction(write=True) as connection:
                cursor = await connection.execute(
                    """
                    UPDATE channel_identities
                    SET binding_status = 'bound', employee_id = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (str(employee_id), timestamp, str(identity_id), expected_revision),
                )
                if cursor.rowcount != 1:
                    await _raise_missing_or_revision(
                        connection, "channel_identities", str(identity_id)
                    )
                await connection.execute(
                    """
                    INSERT INTO agent_sessions(id, employee_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(employee_id) DO NOTHING
                    """,
                    (session_id, str(employee_id), timestamp, timestamp),
                )
                session = await (
                    await connection.execute(
                        "SELECT id FROM agent_sessions WHERE employee_id = ?", (str(employee_id),)
                    )
                ).fetchone()
                assert session is not None
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="identity.bound",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="channel_identity",
                        subject_id=str(identity_id),
                        request_id=request_id,
                        summary={"employee_id": str(employee_id)},
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            raise DirectoryConstraintError("Identity or employee binding is invalid.") from exc
        identity = await self.get(identity_id)
        return identity, UUID(str(session["id"]))

    async def unbind(
        self,
        identity_id: UUID,
        *,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ChannelIdentity:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE channel_identities
                SET binding_status = 'unbound', employee_id = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (format_rfc3339(utc_now()), str(identity_id), expected_revision),
            )
            if cursor.rowcount != 1:
                await _raise_missing_or_revision(connection, "channel_identities", str(identity_id))
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="identity.unbound",
                    actor_type="admin",
                    actor_id=actor_id,
                    subject_type="channel_identity",
                    subject_id=str(identity_id),
                    request_id=request_id,
                    summary={},
                ),
            )
        return await self.get(identity_id)

    async def get(self, identity_id: UUID) -> ChannelIdentity:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM channel_identities WHERE id = ?", (str(identity_id),)
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Channel identity")
        return _identity_from_row(row)

    async def resolve_or_register(
        self, *, app_id: str, platform_user_id: str, display_name: str | None
    ) -> IdentityResolution:
        identity = await self.upsert_unbound(
            app_id=app_id, platform_user_id=platform_user_id, display_name=display_name
        )
        async with self.database.transaction(write=True) as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT i.*, e.employee_no, e.display_name AS employee_display_name,
                           e.department, e.manager_employee_id, e.timezone,
                           e.status AS employee_status
                    FROM channel_identities i
                    LEFT JOIN employees e ON e.id = i.employee_id
                    WHERE i.id = ?
                    """,
                    (str(identity.identity_id),),
                )
            ).fetchone()
            assert row is not None
            current_identity = _identity_from_row(row)
            if current_identity.binding_status == "unbound":
                return IdentityResolution(status="unbound", identity=current_identity)
            if row["employee_status"] != "active":
                return IdentityResolution(status="disabled", identity=current_identity)
            timestamp = format_rfc3339(utc_now())
            await connection.execute(
                """
                INSERT INTO agent_sessions(id, employee_id, created_at, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(employee_id) DO NOTHING
                """,
                (str(new_uuid4()), row["employee_id"], timestamp, timestamp),
            )
            session = await (
                await connection.execute(
                    "SELECT id FROM agent_sessions WHERE employee_id = ?", (row["employee_id"],)
                )
            ).fetchone()
            assert session is not None
            actor = ActorContext(
                employee_id=UUID(str(row["employee_id"])),
                employee_no=str(row["employee_no"]),
                display_name=str(row["employee_display_name"]),
                department=str(row["department"]),
                manager_employee_id=(
                    UUID(str(row["manager_employee_id"])) if row["manager_employee_id"] else None
                ),
                timezone=str(row["timezone"]),
                channel_identity_id=current_identity.identity_id,
            )
        return IdentityResolution(
            status="active",
            identity=current_identity,
            actor=actor,
            session_id=UUID(str(session["id"])),
        )

    async def claim_event(self, *, app_id: str, event_id: str) -> bool:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO processed_channel_events(id, channel, app_id, event_id, received_at)
                VALUES (?, 'feishu', ?, ?, ?) ON CONFLICT(channel, app_id, event_id) DO NOTHING
                """,
                (str(new_uuid4()), app_id, event_id, format_rfc3339(utc_now())),
            )
        return cursor.rowcount == 1


async def _identity_row(
    connection: aiosqlite.Connection, app_id: str, platform_user_id: str
) -> aiosqlite.Row:
    row = await (
        await connection.execute(
            """
            SELECT * FROM channel_identities
            WHERE channel = 'feishu' AND app_id = ? AND platform_user_id = ?
            """,
            (app_id, platform_user_id),
        )
    ).fetchone()
    assert row is not None
    return row


async def _raise_missing_or_revision(
    connection: aiosqlite.Connection, table: str, identifier: str
) -> None:
    if table not in {"employees", "channel_identities"}:
        raise ValueError("Unsupported resource table")
    row = await (
        await connection.execute(f"SELECT 1 FROM {table} WHERE id = ?", (identifier,))
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("Resource")
    raise RevisionConflictError()


def _employee_from_row(row: aiosqlite.Row) -> Employee:
    return Employee(
        employee_id=UUID(str(row["id"])),
        employee_no=str(row["employee_no"]),
        display_name=str(row["display_name"]),
        department=str(row["department"]),
        manager_employee_id=(
            UUID(str(row["manager_employee_id"])) if row["manager_employee_id"] else None
        ),
        timezone=str(row["timezone"]),
        status=cast(Literal["active", "disabled"], str(row["status"])),
        revision=int(row["revision"]),
        created_at=parse_rfc3339(str(row["created_at"])),
        updated_at=parse_rfc3339(str(row["updated_at"])),
    )


def _identity_from_row(row: aiosqlite.Row) -> ChannelIdentity:
    return ChannelIdentity(
        identity_id=UUID(str(row["id"])),
        channel=cast(Literal["feishu"], str(row["channel"])),
        app_id=str(row["app_id"]),
        platform_user_id=str(row["platform_user_id"]),
        display_name=str(row["display_name"]) if row["display_name"] is not None else None,
        binding_status=cast(Literal["unbound", "bound"], str(row["binding_status"])),
        employee_id=UUID(str(row["employee_id"])) if row["employee_id"] else None,
        revision=int(row["revision"]),
        created_at=parse_rfc3339(str(row["created_at"])),
        updated_at=parse_rfc3339(str(row["updated_at"])),
    )


def _validate_employee_fields(**fields: Any) -> None:
    for name in ("employee_no", "display_name", "department"):
        if name in fields and (not isinstance(fields[name], str) or not fields[name].strip()):
            raise ValueError(f"{name} must not be empty")
    timezone = fields.get("timezone")
    if timezone is not None:
        try:
            ZoneInfo(str(timezone))
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
