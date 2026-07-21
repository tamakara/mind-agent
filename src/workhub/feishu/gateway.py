import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import lark_oapi as lark  # type: ignore[import-untyped]
from lark_oapi.event.callback.model.p2_card_action_trigger import (  # type: ignore[import-untyped]
    P2CardActionTriggerResponse,
)

from workhub.domain.common import format_rfc3339, utc_now

logger = logging.getLogger(__name__)

ConnectionState = Literal[
    "disabled", "starting", "connected", "reconnecting", "degraded", "stopped"
]


@dataclass(frozen=True, slots=True)
class FeishuConnectionStatus:
    state: ConnectionState
    reconnect_attempt: int
    last_error: str | None
    updated_at: str


class FeishuConnection(Protocol):
    async def connect(self) -> None: ...

    async def wait_until_disconnected(self, stop: threading.Event) -> None: ...

    async def close(self) -> None: ...


class FeishuEventHandler(Protocol):
    async def handle_message_event(self, event: Any) -> str: ...

    async def handle_card_event(self, event: Any) -> str: ...


ConnectionFactory = Callable[[str, str, Any], FeishuConnection]


class OfficialSdkConnection:
    """Stoppable adapter around the official SDK's long-connection client."""

    def __init__(self, app_id: str, app_secret: str, event_handler: Any) -> None:
        from lark_oapi import ws

        self._client = ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            auto_reconnect=False,
        )

    async def connect(self) -> None:
        # lark-oapi keeps its event loop at module scope. Point it at this
        # dedicated connection thread before using its async connection API.
        import lark_oapi.ws.client as sdk_ws  # type: ignore[import-untyped]

        sdk_ws.loop = asyncio.get_running_loop()
        await self._client._connect()

    async def wait_until_disconnected(self, stop: threading.Event) -> None:
        while not stop.is_set() and self._client._conn is not None:
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        await self._client._disconnect()


class FeishuGateway:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        router: FeishuEventHandler,
        *,
        reconnect_attempts: int = 3,
        reconnect_delay_seconds: float = 1.0,
        connection_factory: ConnectionFactory = OfficialSdkConnection,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.router = router
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.connection_factory = connection_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._app_loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[Future[Any]] = set()
        self._pending_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status = FeishuConnectionStatus(
            state="stopped", reconnect_attempt=0, last_error=None, updated_at=_now()
        )

    async def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._app_loop = asyncio.get_running_loop()
        self._stop.clear()
        self._set_status("starting", reconnect_attempt=0, last_error=None)
        self._thread = threading.Thread(
            target=self._thread_main,
            name="workhub-feishu",
            daemon=True,
        )
        self._thread.start()

    async def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join)
        self._thread = None
        pending = self._pending_snapshot()
        if pending:
            await asyncio.gather(
                *(asyncio.wrap_future(item) for item in pending), return_exceptions=True
            )
        self._set_status("stopped", reconnect_attempt=0, last_error=None)

    def status(self) -> FeishuConnectionStatus:
        with self._status_lock:
            return self._status

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_connection())
        except Exception:
            logger.exception("Feishu connection worker failed")

    async def _run_connection(self) -> None:
        dispatcher = self._build_dispatcher()
        connection = self.connection_factory(self.app_id, self.app_secret, dispatcher)
        max_attempts = self.reconnect_attempts + 1
        for attempt in range(max_attempts):
            if self._stop.is_set():
                break
            state: ConnectionState = "starting" if attempt == 0 else "reconnecting"
            self._set_status(state, reconnect_attempt=attempt, last_error=None)
            try:
                await connection.connect()
                self._set_status("connected", reconnect_attempt=attempt, last_error=None)
                await connection.wait_until_disconnected(self._stop)
                if self._stop.is_set():
                    break
                raise ConnectionError("Feishu long connection closed")
            except Exception as exc:
                logger.warning("Feishu connection attempt failed: %s", type(exc).__name__)
                self._set_status(
                    "degraded" if attempt + 1 == max_attempts else "reconnecting",
                    reconnect_attempt=attempt,
                    last_error=type(exc).__name__,
                )
                if attempt + 1 < max_attempts:
                    await self._wait_before_retry()
            finally:
                try:
                    await connection.close()
                except Exception:
                    logger.exception("Failed to close Feishu connection")

    async def _wait_before_retry(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.reconnect_delay_seconds
        while not self._stop.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.1, max(0, deadline - asyncio.get_running_loop().time())))

    def _build_dispatcher(self) -> Any:
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_card_action_trigger(self._on_card_event)
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )

    def _on_message_event(self, event: Any) -> None:
        self._submit(self.router.handle_message_event(event))

    def _on_card_event(self, event: Any) -> P2CardActionTriggerResponse:
        # Card callbacks are registered separately and dispatched before any
        # normal-message path, so they can never enter free-form Agent routing.
        self._submit(self.router.handle_card_event(event))
        return P2CardActionTriggerResponse()

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        loop = self._app_loop
        if loop is None or loop.is_closed() or self._stop.is_set():
            coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        with self._pending_lock:
            self._pending.add(future)
        future.add_done_callback(self._event_done)

    def _event_done(self, future: Future[Any]) -> None:
        with self._pending_lock:
            self._pending.discard(future)
        try:
            future.result()
        except Exception:
            logger.exception("Feishu event handling failed")

    def _pending_snapshot(self) -> tuple[Future[Any], ...]:
        with self._pending_lock:
            return tuple(self._pending)

    def _set_status(
        self, state: ConnectionState, *, reconnect_attempt: int, last_error: str | None
    ) -> None:
        with self._status_lock:
            self._status = FeishuConnectionStatus(
                state=state,
                reconnect_attempt=reconnect_attempt,
                last_error=last_error,
                updated_at=_now(),
            )


def _now() -> str:
    return format_rfc3339(utc_now())
