import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from workhub.audit import AuditWriter
from workhub.context import RecallService, ScrollBuilder, ScrollRepository
from workhub.domain import (
    ActorContext,
    ChannelAddress,
    ChatMessage,
    ChatResponse,
    FeishuMessage,
    MessageSender,
    ToolCall,
)
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.runtime import AgentRuntime, CoreRuntimeToolProvider
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


def _message(open_id: str, value: str) -> FeishuMessage:
    from datetime import UTC, datetime

    return FeishuMessage(
        message_id=f"om_{open_id}_{value}",
        event_id=f"evt_{open_id}_{value}",
        address=ChannelAddress(
            app_id="cli_1", conversation_type="private", conversation_id=f"oc_{open_id}"
        ),
        sender=MessageSender(platform_user_id=open_id),
        created_at=datetime.now(UTC),
        text=value,
    )


class _Transport:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []

    async def reply_text(self, message_id: str, text: str) -> str:
        self.replies.append((message_id, text))
        return "reply"

    async def send_text(self, address: ChannelAddress, text: str) -> str:
        del address, text
        return "message"

    async def send_card(self, address: ChannelAddress, card: dict[str, Any]) -> str:
        del address, card
        return "card"

    async def update_card(self, message_id: str, card: dict[str, Any]) -> None:
        del message_id, card


class _SequenceProvider:
    def __init__(self, responses: list[ChatResponse], *, delay: float = 0) -> None:
        self.responses = responses
        self.delay = delay
        self.calls: list[list[ChatMessage]] = []
        self.active = 0
        self.max_active = 0

    async def complete(self, messages: list[ChatMessage], tools: object = None) -> ChatResponse:
        del tools
        self.calls.append(messages)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return self.responses.pop(0)
        finally:
            self.active -= 1


def _runtime(
    database: Database, provider: _SequenceProvider, transport: _Transport
) -> AgentRuntime:
    repository = ScrollRepository(database)

    async def factory() -> _SequenceProvider:
        return provider

    return AgentRuntime(
        repository,
        ScrollBuilder(repository),
        factory,
        CoreRuntimeToolProvider(RecallService(database)),
        transport,
        max_iterations=4,
        total_timeout_seconds=3,
        tool_timeout_seconds=1,
        context_token_budget=4_000,
    )


async def test_runtime_persists_complete_tool_turn_and_structured_headline(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor, session_id = await _actor_session(database, "E10001", "ou_1")
    provider = _SequenceProvider(
        [
            ChatResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call_1",
                        name="recall_session_history",
                        arguments={"mode": "search", "query": "old leave balance"},
                    ),
                )
            ),
            ChatResponse(
                text=json.dumps(
                    {"response": "No matching history.", "headline": "Searched prior history"}
                )
            ),
        ]
    )
    transport = _Transport()
    runtime = _runtime(database, provider, transport)

    await runtime.handle_message(_message("ou_1", "find old balance"), actor, session_id)

    turns = await ScrollRepository(database).list_turns(actor, session_id)
    assert len(turns) == 1
    assert turns[0].status == "completed"
    assert turns[0].headline == "Searched prior history"
    assert [event.event_type for event in turns[0].events] == [
        "user_message",
        "agent_message",
        "tool_call",
        "tool_result",
        "agent_message",
    ]
    tool_payload = turns[0].events[2].payload
    assert tool_payload is not None
    assert "old leave balance" not in json.dumps(tool_payload)
    assert transport.replies[0][1] == "No matching history."


async def test_runtime_serializes_same_employee_and_allows_different_employees(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor_a, session_a = await _actor_session(database, "E10001", "ou_1")
    actor_b, session_b = await _actor_session(database, "E10002", "ou_2")
    provider = _SequenceProvider(
        [ChatResponse(text='{"response":"ok","headline":"done"}') for _ in range(4)],
        delay=0.05,
    )
    runtime = _runtime(database, provider, _Transport())

    await asyncio.gather(
        runtime.handle_message(_message("ou_1", "a1"), actor_a, session_a),
        runtime.handle_message(_message("ou_1", "a2"), actor_a, session_a),
    )
    assert provider.max_active == 1

    await asyncio.gather(
        runtime.handle_message(_message("ou_1", "a3"), actor_a, session_a),
        runtime.handle_message(_message("ou_2", "b1"), actor_b, session_b),
    )
    assert provider.max_active == 2


async def test_runtime_timeout_completes_failed_turn(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    actor, session_id = await _actor_session(database, "E10001", "ou_1")
    provider = _SequenceProvider(
        [ChatResponse(text='{"response":"late","headline":"late"}')], delay=1
    )
    transport = _Transport()
    runtime = _runtime(database, provider, transport)
    runtime.total_timeout_seconds = 0.2

    await runtime.handle_message(_message("ou_1", "timeout"), actor, session_id)

    turns = await ScrollRepository(database).list_turns(actor, session_id)
    assert turns[0].status == "failed"
    assert turns[0].events[-1].event_type == "runtime_error"
    assert turns[0].events[-1].payload == {
        "error": {"code": "agent_run_failed", "type": "CancelledError"}
    }
    assert transport.replies[0][1] == "对话处理失败。请稍后重试。"
