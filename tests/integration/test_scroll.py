from pathlib import Path
from uuid import UUID

import pytest

from workhub.audit import AuditWriter
from workhub.context import RecallService, ScrollBuilder, ScrollRepository
from workhub.domain import ActorContext
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.errors import ApplicationError
from workhub.storage import Database


async def _actor_session(
    database: Database, number: str, open_id: str
) -> tuple[ActorContext, UUID]:
    audit = AuditWriter(database)
    employees = EmployeeRepository(database, audit)
    identities = IdentityRepository(database, audit)
    employee = await employees.create(
        employee_no=number,
        display_name=number,
        department="Engineering",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        actor_id="admin",
        request_id="create",
    )
    identity = await identities.upsert_unbound(
        app_id="cli_1", platform_user_id=open_id, display_name=None
    )
    _, session_id = await identities.bind(
        identity.identity_id,
        employee.employee_id,
        expected_revision=identity.revision,
        actor_id="admin",
        request_id="bind",
    )
    resolution = await identities.resolve_or_register(
        app_id="cli_1", platform_user_id=open_id, display_name=None
    )
    assert resolution.actor is not None
    return resolution.actor, session_id


async def test_scroll_preserves_complete_turns_and_recall_is_employee_scoped(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor_a, session_a = await _actor_session(database, "E10001", "ou_1")
    actor_b, session_b = await _actor_session(database, "E10002", "ou_2")
    repository = ScrollRepository(database)

    for index in range(3):
        turn = await repository.start_turn(
            actor_a,
            session_a,
            kind="normal",
            first_event_type="user_message",
            first_text=f"private history {index} " + "x" * 80,
        )
        await repository.append_event(
            actor_a,
            session_a,
            turn.turn_id,
            event_type="agent_message",
            text=f"answer {index} " + "y" * 80,
        )
        await repository.complete_turn(actor_a, session_a, turn.turn_id, headline=f"Turn {index}")

    window = await ScrollBuilder(repository).build(actor_a, session_a, token_budget=60)
    assert len(window.turns) == 1
    assert len(window.turns[0].events) == 2
    assert window.compressed_navigation is not None
    assert window.compressed_navigation.startswith("[context compressed]")
    assert "Turn 0" in window.compressed_navigation

    recall = RecallService(database)
    found = await recall.search(actor_a, session_a, query="private history 0")
    assert found and found[0]["events"][0]["text"].startswith("private history 0")
    assert await recall.search(actor_a, session_a, query="Turn 0")
    assert await recall.search(actor_b, session_b, query="private history 0") == []
    async with database.connect() as connection:
        fts = await (
            await connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'session_events_fts'"
            )
        ).fetchone()
    assert fts is not None
    with pytest.raises(ApplicationError, match="not found"):
        await repository.get_turn(actor_b, session_b, window.turns[0].turn_id)

    expanded = await recall.expand(actor_a, session_a, seq_start=1, seq_end=2)
    assert len(expanded) == 1
    assert [event["seq"] for event in expanded[0]["events"]] == [1, 2]
