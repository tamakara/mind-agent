import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from workhub.audit import AuditWriter
from workhub.confirmations import ConfirmationService, PendingActionRepository
from workhub.context import ScrollRepository
from workhub.domain import ActorContext, ChannelAddress, FeishuMessage, MessageSender
from workhub.domain.feishu import FeishuCardAction
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.errors import ApplicationError
from workhub.mcp import McpCallResult, MCPManager, McpRepository
from workhub.mcp.runtime import McpRuntimeToolProvider
from workhub.runtime import RuntimeToolContext
from workhub.storage import Database


class _Transport:
    def __init__(self) -> None:
        self.cards: list[tuple[ChannelAddress, dict[str, Any]]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.texts: list[tuple[ChannelAddress, str]] = []

    async def reply_text(self, message_id: str, text: str) -> str:
        del message_id, text
        return "reply"

    async def send_text(self, address: ChannelAddress, text: str) -> str:
        self.texts.append((address, text))
        return "text"

    async def send_card(self, address: ChannelAddress, card: dict[str, Any]) -> str:
        self.cards.append((address, card))
        return "card_1"

    async def update_card(self, message_id: str, card: dict[str, Any]) -> None:
        self.updates.append((message_id, card))


class _Manager:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], ActorContext, str | None]] = []

    async def call(
        self,
        descriptor: object,
        arguments: dict[str, Any],
        actor: ActorContext,
        *,
        idempotency_key: str | None = None,
    ) -> McpCallResult:
        del descriptor
        self.calls.append((arguments, actor, idempotency_key))
        return McpCallResult(
            content=[{"type": "text", "text": "申请已提交: LR-1001"}],
            is_error=False,
        )


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


async def _configured_tool(
    database: Database, *, effect: str = "confirm"
) -> tuple[McpRepository, object]:
    audit = AuditWriter(database)
    repository = McpRepository(database, audit)
    client = await repository.upsert_client(
        client_id=None,
        client_key="oa",
        name="OA",
        url="http://127.0.0.1:9001/mcp",
        headers={"Authorization": "Bearer secret"},
        enabled=True,
        expected_revision=None,
        actor_id="admin",
        request_id="create",
    )
    stored = await repository.get_client(client.client_id)
    tools = await repository.sync_tools(
        stored,
        [
            {
                "name": "submit_leave_request",
                "description": "提交年假申请",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string"},
                        "days": {"type": "number"},
                        "employee_id": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                    },
                    "required": ["start_date", "days", "employee_id", "idempotency_key"],
                    "additionalProperties": False,
                },
            }
        ],
    )
    descriptor = await repository.update_tool_setting(
        tools[0].tool_id,
        allowlisted=True,
        effect=effect,  # type: ignore[arg-type]
        expected_revision=tools[0].revision,
        actor_id="admin",
        request_id="policy",
    )
    return repository, descriptor


async def test_tool_discovery_defaults_deny_strips_subject_and_hides_headers(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    audit = AuditWriter(database)
    repository = McpRepository(database, audit)
    client = await repository.upsert_client(
        client_id=None,
        client_key="oa",
        name="OA",
        url="http://127.0.0.1:9001/mcp",
        headers={"Authorization": "Bearer top-secret"},
        enabled=True,
        expected_revision=None,
        actor_id="admin",
        request_id="create",
    )
    assert client.headers_configured is True
    assert "top-secret" not in client.model_dump_json()
    tools = await repository.sync_tools(
        await repository.get_client(client.client_id),
        [
            {
                "name": "query_balance",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                        "year": {"type": "integer"},
                    },
                    "required": ["employee_id", "year"],
                },
            }
        ],
    )
    assert tools[0].model_name == "mcp__oa__query_balance"
    assert tools[0].allowlisted is False
    assert tools[0].effect == "deny"
    assert set(tools[0].input_schema["properties"]) == {"year"}
    assert tools[0].input_schema["required"] == ["year"]


async def test_deny_rejects_before_manager_connects(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor, _ = await _actor_session(database, "E10001", "ou_1")
    repository, descriptor = await _configured_tool(database, effect="deny")
    manager = MCPManager(repository)

    with pytest.raises(ApplicationError, match="denied"):
        await manager.call(  # type: ignore[arg-type]
            descriptor, {"start_date": "2026-08-01", "days": 1}, actor
        )
    assert manager.health() == {}


async def test_allow_runtime_tool_executes_without_confirmation_card(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor, session_id = await _actor_session(database, "E10001", "ou_1")
    repository, _ = await _configured_tool(database, effect="allow")
    manager = _Manager()
    transport = _Transport()
    actions = PendingActionRepository(database, AuditWriter(database))
    message = FeishuMessage(
        message_id="om_allow",
        event_id="evt_allow",
        address=ChannelAddress(app_id="cli_1", conversation_type="private", conversation_id="oc_1"),
        sender=MessageSender(platform_user_id="ou_1"),
        created_at=datetime.now(UTC),
        text="查询",
    )
    turn = await ScrollRepository(database).start_turn(
        actor,
        session_id,
        kind="normal",
        first_event_type="user_message",
        first_text=message.text,
    )
    provider = McpRuntimeToolProvider(
        repository,
        manager,
        actions,
        transport,  # type: ignore[arg-type]
    )
    tools = await provider.snapshot(RuntimeToolContext(actor, session_id, turn.turn_id, message))

    result = json.loads(await tools[0].execute({"start_date": "2026-08-01", "days": 1}))
    assert result["is_error"] is False
    assert len(manager.calls) == 1
    assert transport.cards == []


async def test_confirm_is_persistent_isolated_and_executes_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor, session_id = await _actor_session(database, "E10001", "ou_1")
    other, _ = await _actor_session(database, "E10002", "ou_2")
    repository, _ = await _configured_tool(database)
    actions = PendingActionRepository(database, AuditWriter(database))
    manager = _Manager()
    transport = _Transport()
    provider = McpRuntimeToolProvider(
        repository,
        manager,
        actions,
        transport,  # type: ignore[arg-type]
    )
    message = FeishuMessage(
        message_id="om_1",
        event_id="evt_1",
        address=ChannelAddress(app_id="cli_1", conversation_type="private", conversation_id="oc_1"),
        sender=MessageSender(platform_user_id="ou_1"),
        created_at=datetime.now(UTC),
        text="请假",
    )
    turn = await ScrollRepository(database).start_turn(
        actor,
        session_id,
        kind="normal",
        first_event_type="user_message",
        first_text=message.text,
    )
    tools = await provider.snapshot(RuntimeToolContext(actor, session_id, turn.turn_id, message))
    result = json.loads(await tools[0].execute({"start_date": "2026-08-01", "days": 1}))
    assert result["status"] == "confirmation_required"
    assert manager.calls == []
    token = transport.cards[0][1]["elements"][1]["actions"][0]["value"]["token"]
    async with database.connect() as connection:
        row = await (
            await connection.execute("SELECT token_hash, canonical_args_json FROM pending_actions")
        ).fetchone()
    assert row is not None
    assert token not in str(row["token_hash"])
    assert "employee_id" not in str(row["canonical_args_json"])

    service = ConfirmationService(
        actions,
        repository,
        manager,  # type: ignore[arg-type]
        ScrollRepository(database),
        transport,
    )
    callback = FeishuCardAction(
        event_id="card_1",
        app_id="cli_1",
        platform_user_id="ou_1",
        message_id="card_1",
        conversation_id="oc_1",
        action_token=token,
        decision="confirm",
    )
    wrong_employee = callback.model_copy(update={"event_id": "wrong", "platform_user_id": "ou_2"})
    with pytest.raises(ApplicationError, match="not authorized"):
        await service.handle_card_action(wrong_employee)
    assert other.employee_id != actor.employee_id

    await asyncio.gather(
        service.handle_card_action(callback),
        service.handle_card_action(callback.model_copy(update={"event_id": "card_2"})),
    )
    await service.handle_card_action(callback.model_copy(update={"event_id": "card_3"}))
    assert len(manager.calls) == 1
    assert manager.calls[0][1].employee_id == actor.employee_id
    assert manager.calls[0][2] is not None
    action = await actions.get(UUID(result["action_id"]))
    assert action.status == "succeeded"
    turns = await ScrollRepository(database).list_turns(actor, session_id)
    assert [item.kind for item in turns] == ["normal", "confirmation"]
    assert turns[0].events[-1].event_type == "pending_action_created"
    assert transport.texts[0][1] == "申请已提交: LR-1001"

    expired_result = json.loads(await tools[0].execute({"start_date": "2026-08-02", "days": 1}))
    expired_token = transport.cards[-1][1]["elements"][1]["actions"][0]["value"]["token"]
    async with database.transaction(write=True) as connection:
        await connection.execute(
            "UPDATE pending_actions SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", expired_result["action_id"]),
        )
    await service.handle_card_action(
        callback.model_copy(update={"event_id": "expired", "action_token": expired_token})
    )
    assert (await actions.get(UUID(expired_result["action_id"]))).status == "expired"
    assert len(manager.calls) == 1

    recovery_result = json.loads(await tools[0].execute({"start_date": "2026-08-03", "days": 1}))
    recovery_token = transport.cards[-1][1]["elements"][1]["actions"][0]["value"]["token"]
    recovery_callback = callback.model_copy(
        update={"event_id": "recovery", "action_token": recovery_token}
    )
    recovery_claim = await actions.validate_and_claim(recovery_callback)
    assert recovery_claim.claimed is True
    assert recovery_claim.action.status == "executing"
    await service.recover()
    await service.recover()
    assert len(manager.calls) == 2
    assert manager.calls[-1][2] == recovery_claim.action.idempotency_key
    assert (await actions.get(UUID(recovery_result["action_id"]))).status == "succeeded"

    cancel_result = json.loads(await tools[0].execute({"start_date": "2026-08-04", "days": 1}))
    cancel_token = transport.cards[-1][1]["elements"][1]["actions"][0]["value"]["token"]
    await service.handle_card_action(
        callback.model_copy(
            update={
                "event_id": "cancel",
                "action_token": cancel_token,
                "decision": "cancel",
            }
        )
    )
    assert (await actions.get(UUID(cancel_result["action_id"]))).status == "cancelled"
    assert len(manager.calls) == 2
