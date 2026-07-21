import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import aiosqlite

from mock_oa_service.database import Database
from mock_oa_service.domain import (
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveStatus,
    LeaveSubmission,
    MockOAError,
)

EMPLOYEE_NO_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LeaveRepository:
    def __init__(self, database: Database, *, default_entitlement_days: float = 10) -> None:
        self.database = database
        self.default_entitlement_days = default_entitlement_days

    async def seed_employee(
        self,
        *,
        employee_id: str,
        employee_no: str,
        display_name: str,
        entitlement_days: float | None = None,
        year: int | None = None,
    ) -> None:
        _validate_subject(employee_id, employee_no)
        timestamp = utc_timestamp()
        target_year = year or date.today().year
        entitlement = (
            self.default_entitlement_days if entitlement_days is None else entitlement_days
        )
        async with self.database.transaction(write=True) as connection:
            await _ensure_employee(
                connection,
                employee_id=employee_id,
                employee_no=employee_no,
                display_name=display_name,
                timestamp=timestamp,
            )
            await connection.execute(
                """
                INSERT INTO leave_balances(
                    employee_id, year, entitled_days, used_days, updated_at
                ) VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(employee_id, year) DO NOTHING
                """,
                (employee_id, target_year, entitlement, timestamp),
            )

    async def balance(self, *, employee_id: str, employee_no: str) -> LeaveBalance:
        today = date.today()
        async with self.database.transaction(write=True) as connection:
            await self._ensure_subject(connection, employee_id, employee_no, today.year)
            row = await (
                await connection.execute(
                    """
                    SELECT entitled_days - used_days AS remaining_days, updated_at
                    FROM leave_balances WHERE employee_id = ? AND year = ?
                    """,
                    (employee_id, today.year),
                )
            ).fetchone()
        assert row is not None
        return LeaveBalance(
            remaining_days=float(row["remaining_days"]),
            as_of=today.isoformat(),
        )

    async def submit(
        self,
        *,
        employee_id: str,
        employee_no: str,
        start_date: str,
        end_date: str,
        leave_type: Literal["annual_leave"],
        reason: str | None,
        idempotency_key: str,
    ) -> LeaveSubmission:
        if not idempotency_key or len(idempotency_key) > 200:
            raise MockOAError("invalid_idempotency_key", "A valid idempotency key is required.")
        timestamp = utc_timestamp()
        async with self.database.transaction(write=True) as connection:
            existing = await (
                await connection.execute(
                    """
                    SELECT r.* FROM idempotency_records i
                    JOIN leave_requests r ON r.request_id = i.request_id
                    WHERE i.employee_id = ? AND i.idempotency_key = ?
                    """,
                    (employee_id, idempotency_key),
                )
            ).fetchone()
            if existing is not None:
                return _submission(existing)
            start = _parse_date(start_date, "start_date")
            end = _parse_date(end_date, "end_date")
            if end < start:
                raise MockOAError("invalid_date_range", "end_date must not precede start_date.")
            days_by_year = _working_days_by_year(start, end)
            days = float(sum(days_by_year.values()))
            if days <= 0:
                raise MockOAError(
                    "no_working_days",
                    "Leave request must include at least one working day.",
                )
            for year in days_by_year:
                await self._ensure_subject(connection, employee_id, employee_no, year)
            overlap = await (
                await connection.execute(
                    """
                    SELECT request_id FROM leave_requests
                    WHERE employee_id = ? AND status IN ('pending_approval', 'approved')
                      AND start_date <= ? AND end_date >= ? LIMIT 1
                    """,
                    (employee_id, end.isoformat(), start.isoformat()),
                )
            ).fetchone()
            if overlap is not None:
                raise MockOAError(
                    "leave_request_overlap",
                    "Leave request overlaps an existing active request.",
                    status_code=409,
                )
            for year, year_days in days_by_year.items():
                balance = await (
                    await connection.execute(
                        """
                        SELECT entitled_days, used_days FROM leave_balances
                        WHERE employee_id = ? AND year = ?
                        """,
                        (employee_id, year),
                    )
                ).fetchone()
                assert balance is not None
                remaining = float(balance["entitled_days"]) - float(balance["used_days"])
                if remaining < year_days:
                    raise MockOAError(
                        "insufficient_leave_balance",
                        "Annual leave balance is insufficient.",
                        status_code=409,
                    )
            request_id = f"LR-{start.year}-{uuid4().hex[:12].upper()}"
            await connection.execute(
                """
                INSERT INTO leave_requests(
                    request_id, employee_id, start_date, end_date, leave_type,
                    reason, working_days, status, submitted_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?)
                """,
                (
                    request_id,
                    employee_id,
                    start.isoformat(),
                    end.isoformat(),
                    leave_type,
                    reason.strip() if reason and reason.strip() else None,
                    days,
                    timestamp,
                    timestamp,
                ),
            )
            await connection.execute(
                """
                INSERT INTO idempotency_records(
                    employee_id, idempotency_key, request_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (employee_id, idempotency_key, request_id, timestamp),
            )
            for year, year_days in days_by_year.items():
                await connection.execute(
                    """
                    UPDATE leave_balances SET used_days = used_days + ?, updated_at = ?
                    WHERE employee_id = ? AND year = ?
                    """,
                    (year_days, timestamp, employee_id, year),
                )
            row = await _request_row(connection, request_id)
        assert row is not None
        return _submission(row)

    async def status(
        self, *, employee_id: str, employee_no: str, request_id: str
    ) -> LeaveRequestStatus:
        async with self.database.transaction(write=True) as connection:
            await self._ensure_subject(connection, employee_id, employee_no, date.today().year)
            row = await (
                await connection.execute(
                    """
                    SELECT request_id, status, updated_at FROM leave_requests
                    WHERE request_id = ? AND employee_id = ?
                    """,
                    (request_id, employee_id),
                )
            ).fetchone()
        if row is None:
            raise MockOAError(
                "leave_request_not_found", "Leave request was not found.", status_code=404
            )
        return LeaveRequestStatus(
            request_id=str(row["request_id"]),
            status=cast(LeaveStatus, str(row["status"])),
            updated_at=str(row["updated_at"]),
        )

    async def update_status(
        self,
        request_id: str,
        status: Literal["approved", "rejected"],
    ) -> LeaveRequest:
        timestamp = utc_timestamp()
        async with self.database.transaction(write=True) as connection:
            row = await _request_row(connection, request_id)
            if row is None:
                raise MockOAError(
                    "leave_request_not_found", "Leave request was not found.", status_code=404
                )
            current = str(row["status"])
            if current == status:
                return _request(row)
            if current != "pending_approval":
                raise MockOAError(
                    "invalid_status_transition",
                    "Only pending requests can be approved or rejected.",
                    status_code=409,
                )
            await connection.execute(
                "UPDATE leave_requests SET status = ?, updated_at = ? WHERE request_id = ?",
                (status, timestamp, request_id),
            )
            if status == "rejected":
                days_by_year = _working_days_by_year(
                    date.fromisoformat(str(row["start_date"])),
                    date.fromisoformat(str(row["end_date"])),
                )
                for year, year_days in days_by_year.items():
                    await connection.execute(
                        """
                        UPDATE leave_balances SET used_days = used_days - ?, updated_at = ?
                        WHERE employee_id = ? AND year = ?
                        """,
                        (year_days, timestamp, str(row["employee_id"]), year),
                    )
            updated = await _request_row(connection, request_id)
        assert updated is not None
        return _request(updated)

    async def _ensure_subject(
        self,
        connection: aiosqlite.Connection,
        employee_id: str,
        employee_no: str,
        year: int,
    ) -> None:
        _validate_subject(employee_id, employee_no)
        timestamp = utc_timestamp()
        await _ensure_employee(
            connection,
            employee_id=employee_id,
            employee_no=employee_no,
            display_name=employee_no,
            timestamp=timestamp,
        )
        await connection.execute(
            """
            INSERT INTO leave_balances(
                employee_id, year, entitled_days, used_days, updated_at
            ) VALUES (?, ?, ?, 0, ?) ON CONFLICT(employee_id, year) DO NOTHING
            """,
            (employee_id, year, self.default_entitlement_days, timestamp),
        )


def _validate_subject(employee_id: str, employee_no: str) -> None:
    try:
        parsed_employee_id = UUID(employee_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise MockOAError(
            "invalid_trusted_subject",
            "Trusted employee ID must be a UUID4.",
            status_code=403,
        ) from exc
    if parsed_employee_id.version != 4 or not EMPLOYEE_NO_PATTERN.fullmatch(employee_no):
        raise MockOAError(
            "invalid_trusted_subject",
            "Trusted employee subject is invalid.",
            status_code=403,
        )


async def _ensure_employee(
    connection: aiosqlite.Connection,
    *,
    employee_id: str,
    employee_no: str,
    display_name: str,
    timestamp: str,
) -> None:
    try:
        await connection.execute(
            """
            INSERT INTO employees(employee_id, employee_no, display_name, created_at)
            VALUES (?, ?, ?, ?) ON CONFLICT(employee_id) DO NOTHING
            """,
            (employee_id, employee_no, display_name, timestamp),
        )
    except aiosqlite.IntegrityError as exc:
        raise MockOAError(
            "employee_subject_conflict",
            "Employee number is already associated with another subject.",
            status_code=409,
        ) from exc
    row = await (
        await connection.execute(
            "SELECT employee_no FROM employees WHERE employee_id = ?", (employee_id,)
        )
    ).fetchone()
    if row is None or str(row["employee_no"]) != employee_no:
        raise MockOAError(
            "employee_subject_conflict",
            "Employee subject does not match the existing employee number.",
            status_code=409,
        )


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MockOAError("invalid_date", f"{field} must be an ISO calendar date.") from exc


def _working_days_by_year(start: date, end: date) -> dict[int, int]:
    totals: dict[int, int] = {}
    current = start
    while current <= end:
        if current.weekday() < 5:
            totals[current.year] = totals.get(current.year, 0) + 1
        current += timedelta(days=1)
    return totals


async def _request_row(connection: aiosqlite.Connection, request_id: str) -> aiosqlite.Row | None:
    return await (
        await connection.execute("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,))
    ).fetchone()


def _submission(row: aiosqlite.Row) -> LeaveSubmission:
    return LeaveSubmission(
        request_id=str(row["request_id"]),
        status="pending_approval",
        submitted_at=str(row["submitted_at"]),
    )


def _request(row: aiosqlite.Row) -> LeaveRequest:
    return LeaveRequest(
        request_id=str(row["request_id"]),
        employee_id=str(row["employee_id"]),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
        leave_type="annual_leave",
        reason=str(row["reason"]) if row["reason"] is not None else None,
        working_days=float(row["working_days"]),
        status=cast(LeaveStatus, str(row["status"])),
        submitted_at=str(row["submitted_at"]),
        updated_at=str(row["updated_at"]),
    )
