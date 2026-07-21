import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mock_oa_service.app import create_app
from mock_oa_service.config import MockOASettings
from mock_oa_service.database import Database
from mock_oa_service.domain import MockOAError
from mock_oa_service.repository import LeaveRepository

EMPLOYEE_1 = "10000000-0000-4000-8000-000000000001"
EMPLOYEE_2 = "10000000-0000-4000-8000-000000000002"
EMPLOYEE_MCP = "10000000-0000-4000-8000-000000000003"


def _weekdays(count: int, *, offset_weeks: int = 0) -> tuple[str, str]:
    year = date.today().year
    current = date(year, 6, 1)
    while current.weekday() != 0:
        current = current.replace(day=current.day + 1)
    current = current.replace(day=current.day + offset_weeks * 7)
    end = current
    found = 1
    while found < count:
        end = end.replace(day=end.day + 1)
        if end.weekday() < 5:
            found += 1
    return current.isoformat(), end.isoformat()


async def _repository(
    tmp_path: Path, *, entitlement: float = 10
) -> tuple[Database, LeaveRepository]:
    database = Database(tmp_path / "mock_oa.db")
    await database.migrate()
    return database, LeaveRepository(database, default_entitlement_days=entitlement)


async def test_database_migration_pragmas_and_seed_are_idempotent(tmp_path: Path) -> None:
    database, repository = await _repository(tmp_path)
    await database.migrate()
    await repository.seed_employee(
        employee_id=EMPLOYEE_1,
        employee_no="E10001",
        display_name="Employee",
        entitlement_days=8,
    )
    await repository.seed_employee(
        employee_id=EMPLOYEE_1,
        employee_no="E10001",
        display_name="Employee",
        entitlement_days=8,
    )

    async with database.connect() as connection:
        tables = {
            str(row["name"])
            for row in await (
                await connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        journal = await (await connection.execute("PRAGMA journal_mode")).fetchone()
        foreign_keys = await (await connection.execute("PRAGMA foreign_keys")).fetchone()
        employees = await (await connection.execute("SELECT COUNT(*) FROM employees")).fetchone()

    assert tables >= {
        "employees",
        "leave_balances",
        "leave_requests",
        "idempotency_records",
    }
    assert journal[0] == "wal"
    assert foreign_keys[0] == 1
    assert employees[0] == 1


async def test_concurrent_idempotent_submission_deducts_balance_once(tmp_path: Path) -> None:
    _, repository = await _repository(tmp_path)
    start, end = _weekdays(5)
    arguments = {
        "employee_id": EMPLOYEE_1,
        "employee_no": "E10001",
        "start_date": start,
        "end_date": end,
        "leave_type": "annual_leave",
        "reason": "休假",
        "idempotency_key": "same-key",
    }

    first, second = await asyncio.gather(
        repository.submit(**arguments),
        repository.submit(**arguments),
    )
    retry_with_changed_arguments = await repository.submit(
        **{**arguments, "start_date": "invalid", "end_date": "invalid"}
    )
    balance = await repository.balance(employee_id=EMPLOYEE_1, employee_no="E10001")

    assert first == second == retry_with_changed_arguments
    assert first.status == "pending_approval"
    assert balance.remaining_days == 5


async def test_submission_validates_dates_balance_and_overlap(tmp_path: Path) -> None:
    _, repository = await _repository(tmp_path, entitlement=5)
    start, end = _weekdays(5)
    common = {
        "employee_id": EMPLOYEE_1,
        "employee_no": "E10001",
        "leave_type": "annual_leave",
        "reason": None,
    }
    await repository.submit(
        **common,
        start_date=start,
        end_date=end,
        idempotency_key="first",
    )

    with pytest.raises(MockOAError, match="overlaps"):
        await repository.submit(
            **common,
            start_date=start,
            end_date=end,
            idempotency_key="overlap",
        )
    other_start, other_end = _weekdays(1, offset_weeks=2)
    with pytest.raises(MockOAError, match="insufficient"):
        await repository.submit(
            **common,
            start_date=other_start,
            end_date=other_end,
            idempotency_key="insufficient",
        )
    with pytest.raises(MockOAError, match="must not precede"):
        await repository.submit(
            **common,
            start_date=end,
            end_date=start,
            idempotency_key="backwards",
        )
    weekend = date(date.today().year, 6, 6)
    while weekend.weekday() != 5:
        weekend = weekend.replace(day=weekend.day + 1)
    with pytest.raises(MockOAError, match="working day"):
        await repository.submit(
            **common,
            start_date=weekend.isoformat(),
            end_date=weekend.isoformat(),
            idempotency_key="weekend",
        )


async def test_status_is_employee_scoped_and_rejection_restores_balance(tmp_path: Path) -> None:
    _, repository = await _repository(tmp_path)
    start, end = _weekdays(2)
    submitted = await repository.submit(
        employee_id=EMPLOYEE_1,
        employee_no="E10001",
        start_date=start,
        end_date=end,
        leave_type="annual_leave",
        reason=None,
        idempotency_key="request-1",
    )
    with pytest.raises(MockOAError, match="not found"):
        await repository.status(
            employee_id=EMPLOYEE_2,
            employee_no="E10002",
            request_id=submitted.request_id,
        )

    rejected = await repository.update_status(submitted.request_id, "rejected")
    repeated = await repository.update_status(submitted.request_id, "rejected")
    balance = await repository.balance(employee_id=EMPLOYEE_1, employee_no="E10001")

    assert rejected.status == repeated.status == "rejected"
    assert balance.remaining_days == 10
    with pytest.raises(MockOAError, match="Only pending"):
        await repository.update_status(submitted.request_id, "approved")


async def test_invalid_trusted_employee_subject_is_rejected(tmp_path: Path) -> None:
    _, repository = await _repository(tmp_path)
    with pytest.raises(MockOAError, match="UUID4"):
        await repository.balance(employee_id="employee-1", employee_no="E10001")
    with pytest.raises(MockOAError, match="invalid"):
        await repository.balance(employee_id=EMPLOYEE_1, employee_no="not allowed")


def test_demo_admin_requires_token_and_never_exposes_status_change_as_mcp_tool(
    tmp_path: Path,
) -> None:
    settings = MockOASettings(
        data_dir=tmp_path / "mock-oa",
        admin_token="admin-secret",
        workhub_shared_secret="shared-secret",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        repository: LeaveRepository = app.state.leave_repository
        start, end = _weekdays(1)
        submitted = asyncio.run(
            repository.submit(
                employee_id=EMPLOYEE_1,
                employee_no="E20001",
                start_date=start,
                end_date=end,
                leave_type="annual_leave",
                reason=None,
                idempotency_key="admin-test",
            )
        )
        unauthenticated = client.patch(
            f"/api/v1/leave-requests/{submitted.request_id}/status",
            json={"status": "approved"},
        )
        approved = client.patch(
            f"/api/v1/leave-requests/{submitted.request_id}/status",
            headers={"Authorization": "Bearer admin-secret"},
            json={"status": "approved"},
        )

        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "admin_auth_failed"
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        initialized = client.post(
            "/mcp",
            headers={
                "X-WorkHub-Shared-Secret": "shared-secret",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        session_id = initialized.headers["mcp-session-id"]
        tools = client.post(
            "/mcp",
            headers={
                "X-WorkHub-Shared-Secret": "shared-secret",
                "Mcp-Session-Id": session_id,
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        names = {item["name"] for item in tools.json()["result"]["tools"]}
        assert names == {
            "query_leave_balance",
            "submit_leave_request",
            "query_leave_request_status",
        }
        assert "update_leave_status" not in names
        called = client.post(
            "/mcp",
            headers={
                "X-WorkHub-Shared-Secret": "shared-secret",
                "Mcp-Session-Id": session_id,
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "query_leave_balance",
                    "arguments": {
                        "employee_id": EMPLOYEE_MCP,
                        "employee_no": "E30001",
                    },
                },
            },
        )
        assert called.status_code == 200
        assert called.json()["result"]["isError"] is False
        assert called.json()["result"]["structuredContent"]["remaining_days"] == 10


def test_mcp_rejects_missing_or_invalid_shared_secret(tmp_path: Path) -> None:
    app = create_app(
        MockOASettings(
            data_dir=tmp_path / "mock-oa",
            workhub_shared_secret="shared-secret",
        )
    )
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        missing = client.post("/mcp", json={})
        invalid = client.post("/mcp", headers={"X-WorkHub-Shared-Secret": "wrong"}, json={})
    assert missing.status_code == 401
    assert invalid.status_code == 401
