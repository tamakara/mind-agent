import asyncio
from pathlib import Path

from workhub.audit import AuditWriter
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.storage import Database


async def _repositories(
    tmp_path: Path,
) -> tuple[Database, EmployeeRepository, IdentityRepository]:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    audit = AuditWriter(database)
    return database, EmployeeRepository(database, audit), IdentityRepository(database, audit)


async def _employee(repository: EmployeeRepository, number: str):
    return await repository.create(
        employee_no=number,
        display_name=number,
        department="Engineering",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        actor_id="admin-1",
        request_id="request-1",
    )


async def test_concurrent_unknown_identity_upsert_creates_no_session(tmp_path: Path) -> None:
    database, _, identities = await _repositories(tmp_path)

    resolutions = await asyncio.gather(
        *(
            identities.resolve_or_register(
                app_id="cli_1", platform_user_id="ou_unknown", display_name="Unknown"
            )
            for _ in range(5)
        )
    )

    assert {item.status for item in resolutions} == {"unbound"}
    assert len({item.identity.identity_id for item in resolutions}) == 1
    async with database.connect() as connection:
        identity_count = await (
            await connection.execute("SELECT COUNT(*) FROM channel_identities")
        ).fetchone()
        session_count = await (
            await connection.execute("SELECT COUNT(*) FROM agent_sessions")
        ).fetchone()
    assert identity_count[0] == 1
    assert session_count[0] == 0


async def test_binding_reuses_session_and_unbind_preserves_history_owner(tmp_path: Path) -> None:
    database, employees, identities = await _repositories(tmp_path)
    employee = await _employee(employees, "E10001")
    identity = await identities.upsert_unbound(
        app_id="cli_1", platform_user_id="ou_1", display_name="Employee"
    )

    bound, session_id = await identities.bind(
        identity.identity_id,
        employee.employee_id,
        expected_revision=identity.revision,
        actor_id="admin-1",
        request_id="request-bind",
    )
    first = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id="ou_1", display_name="Employee"
    )
    second = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id="ou_1", display_name="Employee"
    )

    assert bound.binding_status == "bound"
    assert first.status == second.status == "active"
    assert first.session_id == second.session_id == session_id
    assert first.actor is not None and first.actor.employee_id == employee.employee_id

    current = await identities.get(identity.identity_id)
    unbound = await identities.unbind(
        identity.identity_id,
        expected_revision=current.revision,
        actor_id="admin-1",
        request_id="request-unbind",
    )
    resolution = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id="ou_1", display_name="Employee"
    )
    assert unbound.employee_id is None
    assert resolution.status == "unbound"
    async with database.connect() as connection:
        owner = await (
            await connection.execute(
                "SELECT employee_id FROM agent_sessions WHERE id = ?", (str(session_id),)
            )
        ).fetchone()
    assert owner[0] == str(employee.employee_id)


async def test_disabled_employee_and_cross_identity_sessions_are_isolated(tmp_path: Path) -> None:
    _, employees, identities = await _repositories(tmp_path)
    employee_a = await _employee(employees, "E10001")
    employee_b = await _employee(employees, "E10002")
    identity_a = await identities.upsert_unbound(
        app_id="cli_1", platform_user_id="ou_1", display_name=None
    )
    identity_b = await identities.upsert_unbound(
        app_id="cli_1", platform_user_id="ou_2", display_name=None
    )
    _, session_a = await identities.bind(
        identity_a.identity_id,
        employee_a.employee_id,
        expected_revision=identity_a.revision,
        actor_id="admin-1",
        request_id="bind-a",
    )
    _, session_b = await identities.bind(
        identity_b.identity_id,
        employee_b.employee_id,
        expected_revision=identity_b.revision,
        actor_id="admin-1",
        request_id="bind-b",
    )
    assert session_a != session_b

    employee_a = await employees.update(
        employee_a.employee_id,
        expected_revision=employee_a.revision,
        changes={"status": "disabled"},
        actor_id="admin-1",
        request_id="disable-a",
    )
    disabled = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id="ou_1", display_name=None
    )
    active = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id="ou_2", display_name=None
    )
    assert employee_a.status == "disabled"
    assert disabled.status == "disabled" and disabled.actor is None
    assert active.status == "active" and active.actor is not None
    assert active.actor.employee_id == employee_b.employee_id


async def test_duplicate_channel_event_has_single_claim_winner(tmp_path: Path) -> None:
    _, _, identities = await _repositories(tmp_path)

    claims = await asyncio.gather(
        *(identities.claim_event(app_id="cli_1", event_id="evt_1") for _ in range(5))
    )

    assert claims.count(True) == 1
    assert claims.count(False) == 4
