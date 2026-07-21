import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

from workhub.feishu.conversion import convert_card_event, convert_message_event
from workhub.feishu.gateway import FeishuGateway
from workhub.feishu.transport import OfficialFeishuTransport


def _message_event(*, chat_type: str = "p2p", message_type: str = "text") -> Any:
    return SimpleNamespace(
        header=SimpleNamespace(app_id="cli_1", event_id="evt_1", create_time="0"),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
            message=SimpleNamespace(
                message_id="om_1",
                chat_id="oc_1",
                chat_type=chat_type,
                message_type=message_type,
                create_time="1784592000000",
                content=json.dumps({"text": "  hello  "}),
            ),
        ),
    )


def _card_event() -> Any:
    return SimpleNamespace(
        header=SimpleNamespace(app_id="cli_1", event_id="evt_card"),
        event=SimpleNamespace(
            operator=SimpleNamespace(open_id="ou_1"),
            context=SimpleNamespace(open_message_id="om_card", open_chat_id="oc_1"),
            action=SimpleNamespace(value={"decision": "confirm", "token": "secret-token"}),
        ),
    )


def test_message_conversion_accepts_only_private_text() -> None:
    supported = convert_message_event(_message_event())
    group = convert_message_event(_message_event(chat_type="group"))
    non_text = convert_message_event(_message_event(message_type="image"))

    assert supported.status == "supported"
    assert supported.message is not None
    assert supported.message.text == "hello"
    assert supported.message.address.conversation_id == "oc_1"
    assert group.status == "group" and group.message is None
    assert non_text.status == "non_text" and non_text.message is None


def test_card_conversion_accepts_only_known_decisions_and_trusted_address() -> None:
    action = convert_card_event(_card_event())
    invalid = _card_event()
    invalid.event.action.value["decision"] = "approve"

    assert action is not None
    assert action.decision == "confirm"
    assert action.platform_user_id == "ou_1"
    assert action.conversation_id == "oc_1"
    assert convert_card_event(invalid) is None


class _Router:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.message_seen = asyncio.Event()
        self.card_seen = asyncio.Event()

    async def handle_message_event(self, event: Any) -> str:
        del event
        self.calls.append("message")
        self.message_seen.set()
        return "processed"

    async def handle_card_event(self, event: Any) -> str:
        del event
        self.calls.append("card")
        self.card_seen.set()
        return "processed"


class _Connection:
    def __init__(self, dispatcher: Any, failures: int = 0) -> None:
        self.dispatcher = dispatcher
        self.failures = failures
        self.connect_count = 0
        self.close_count = 0

    async def connect(self) -> None:
        self.connect_count += 1
        if self.connect_count <= self.failures:
            raise ConnectionError("offline")

    async def wait_until_disconnected(self, stop: threading.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        self.close_count += 1


async def test_gateway_dispatches_sdk_callbacks_and_closes_gracefully() -> None:
    router = _Router()
    holder: dict[str, _Connection] = {}

    def factory(app_id: str, app_secret: str, dispatcher: Any) -> _Connection:
        assert (app_id, app_secret) == ("cli_1", "secret")
        holder["connection"] = _Connection(dispatcher)
        return holder["connection"]

    gateway = FeishuGateway(
        "cli_1", "secret", router, reconnect_delay_seconds=0, connection_factory=factory
    )
    await gateway.start()
    await asyncio.sleep(0.05)
    connection = holder["connection"]
    message_processor = connection.dispatcher._processorMap["p2.im.message.receive_v1"]
    card_processor = connection.dispatcher._callback_processor_map["p2.card.action.trigger"]

    message_processor.do(_message_event())
    card_processor.do(_card_event())
    await asyncio.wait_for(router.message_seen.wait(), 1)
    await asyncio.wait_for(router.card_seen.wait(), 1)

    assert sorted(router.calls) == ["card", "message"]
    assert gateway.status().state == "connected"
    await asyncio.wait_for(gateway.close(), 1)
    assert connection.close_count >= 1
    assert gateway.status().state == "stopped"


async def test_gateway_limits_reconnect_attempts() -> None:
    router = _Router()
    holder: dict[str, _Connection] = {}

    def factory(app_id: str, app_secret: str, dispatcher: Any) -> _Connection:
        del app_id, app_secret
        holder["connection"] = _Connection(dispatcher, failures=10)
        return holder["connection"]

    gateway = FeishuGateway(
        "cli_1",
        "secret",
        router,
        reconnect_attempts=2,
        reconnect_delay_seconds=0,
        connection_factory=factory,
    )
    await gateway.start()
    for _ in range(100):
        if gateway.status().state == "degraded":
            break
        await asyncio.sleep(0.01)

    assert holder["connection"].connect_count == 3
    assert gateway.status().state == "degraded"
    assert gateway.status().last_error == "ConnectionError"
    await gateway.close()


def test_transport_uses_current_message_or_persisted_address() -> None:
    annotations = OfficialFeishuTransport.send_text.__annotations__
    assert annotations["address"].__name__ == "ChannelAddress"
