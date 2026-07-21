import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from workhub.audit import AuditWriter
from workhub.domain import ActorContext, ChannelAddress, FeishuMessage
from workhub.domain.feishu import FeishuCardAction
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.feishu.router import FeishuEventRouter
from workhub.storage import Database


def _event(event_id: str, open_id: str, *, chat_type: str = "p2p") -> Any:
    return SimpleNamespace(
        header=SimpleNamespace(app_id="cli_1", event_id=event_id),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id)),
            message=SimpleNamespace(
                message_id=f"om_{event_id}",
                chat_id=f"oc_{open_id}",
                chat_type=chat_type,
                message_type="text",
                create_time="1784592000000",
                content=json.dumps({"text": "hello"}),
            ),
        ),
    )


class _Transport:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []

    async def reply_text(self, message_id: str, text: str) -> str:
        self.replies.append((message_id, text))
        return "reply_1"

    async def send_text(self, address: ChannelAddress, text: str) -> str:
        del address, text
        return "message_1"

    async def send_card(self, address: ChannelAddress, card: dict[str, Any]) -> str:
        del address, card
        return "card_1"

    async def update_card(self, message_id: str, card: dict[str, Any]) -> None:
        del message_id, card


class _Messages:
    def __init__(self) -> None:
        self.calls: list[tuple[FeishuMessage, ActorContext, object]] = []

    async def handle_message(
        self, message: FeishuMessage, actor: ActorContext, session_id: object
    ) -> None:
        self.calls.append((message, actor, session_id))


class _Cards:
    def __init__(self) -> None:
        self.actions: list[FeishuCardAction] = []

    async def handle_card_action(self, action: FeishuCardAction) -> None:
        self.actions.append(action)


async def test_unknown_bound_and_duplicate_messages_obey_identity_boundary(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    audit = AuditWriter(database)
    employees = EmployeeRepository(database, audit)
    identities = IdentityRepository(database, audit)
    transport = _Transport()
    messages = _Messages()
    router = FeishuEventRouter(identities, transport, audit, message_handler=messages)

    assert await router.handle_message_event(_event("unknown", "ou_unknown")) == "rejected"
    assert len(messages.calls) == 0
    async with database.connect() as connection:
        sessions = await (
            await connection.execute("SELECT COUNT(*) FROM agent_sessions")
        ).fetchone()
    assert sessions is not None
    assert sessions[0] == 0
    assert "尚未绑定" in transport.replies[0][1]

    employee = await employees.create(
        employee_no="E10001",
        display_name="Employee",
        department="Engineering",
        manager_employee_id=None,
        timezone="Asia/Shanghai",
        actor_id="admin",
        request_id="create",
    )
    identity = await identities.upsert_unbound(
        app_id="cli_1", platform_user_id="ou_bound", display_name=None
    )
    _, session_id = await identities.bind(
        identity.identity_id,
        employee.employee_id,
        expected_revision=identity.revision,
        actor_id="admin",
        request_id="bind",
    )

    event = _event("bound", "ou_bound")
    assert await router.handle_message_event(event) == "processed"
    assert await router.handle_message_event(event) == "duplicate"
    assert len(messages.calls) == 1
    assert messages.calls[0][1].employee_id == employee.employee_id
    assert messages.calls[0][2] == session_id


async def test_group_message_is_ignored_before_identity_or_agent(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    await database.migrate()
    audit = AuditWriter(database)
    identities = IdentityRepository(database, audit)
    transport = _Transport()
    messages = _Messages()
    router = FeishuEventRouter(identities, transport, audit, message_handler=messages)

    result = await router.handle_message_event(_event("group", "ou_1", chat_type="group"))
    assert result == "ignored"
    assert messages.calls == []
    async with database.connect() as connection:
        identities_count = await (
            await connection.execute("SELECT COUNT(*) FROM channel_identities")
        ).fetchone()
    assert identities_count is not None
    assert identities_count[0] == 0
