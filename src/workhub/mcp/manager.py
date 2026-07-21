import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from workhub.domain import ActorContext, ToolDescriptor
from workhub.domain.tools import inject_trusted_actor
from workhub.errors import ApplicationError
from workhub.mcp.repository import McpRepository, StoredMcpClient


@dataclass(frozen=True, slots=True)
class McpCallResult:
    content: list[dict[str, Any]]
    is_error: bool

    def as_json(self) -> str:
        return json.dumps(
            {"content": self.content, "is_error": self.is_error},
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ClientHealth:
    state: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _CallCommand:
    name: str
    arguments: dict[str, Any]
    future: asyncio.Future[Any]


class _Connection:
    def __init__(self, stored: StoredMcpClient, *, timeout_seconds: float) -> None:
        self.stored = stored
        self.timeout_seconds = timeout_seconds
        loop = asyncio.get_running_loop()
        self._ready: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        self._commands: asyncio.Queue[_CallCommand | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run(), name=f"mcp-client-{stored.public.client_key}")

    async def discover(self) -> list[dict[str, Any]]:
        return await self._ready

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._task.done():
            raise RuntimeError("MCP client connection is closed")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._commands.put(_CallCommand(name, arguments, future))
        return await future

    async def close(self, *, force: bool = False) -> None:
        if force and not self._task.done():
            self._task.cancel()
        elif not self._task.done():
            await self._commands.put(None)
        await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        failure: BaseException | None = None
        try:
            async with (
                streamablehttp_client(
                    str(self.stored.public.url),
                    headers=self.stored.headers,
                    timeout=self.timeout_seconds,
                    sse_read_timeout=self.timeout_seconds,
                ) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                result = await session.list_tools()
                discovered = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    }
                    for tool in result.tools
                ]
                if not self._ready.done():
                    self._ready.set_result(discovered)
                while True:
                    command = await self._commands.get()
                    if command is None:
                        break
                    try:
                        value = await session.call_tool(
                            command.name,
                            command.arguments,
                            read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                        )
                    except Exception as exc:
                        if not command.future.done():
                            command.future.set_exception(exc)
                    else:
                        if not command.future.done():
                            command.future.set_result(value)
        except BaseException as exc:
            failure = exc
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            unavailable = failure or RuntimeError("MCP client connection closed")
            while not self._commands.empty():
                command = self._commands.get_nowait()
                if command is not None and not command.future.done():
                    command.future.set_exception(unavailable)


class MCPManager:
    def __init__(self, repository: McpRepository, *, timeout_seconds: float = 15) -> None:
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self._connections: dict[str, _Connection] = {}
        self._health: dict[str, ClientHealth] = {}
        self._guard = asyncio.Lock()

    async def start(self) -> None:
        clients = await self.repository.enabled_clients()
        await asyncio.gather(
            *(self.reconnect(client.public.client_id) for client in clients), return_exceptions=True
        )

    async def close(self) -> None:
        async with self._guard:
            connections = list(self._connections.values())
            self._connections.clear()
        await asyncio.gather(*(item.close() for item in connections), return_exceptions=True)

    def health(self) -> dict[str, ClientHealth]:
        return dict(self._health)

    async def reconnect(self, client_id: UUID) -> list[ToolDescriptor]:
        stored = await self.repository.get_client(client_id)
        await self.disconnect(stored.public.client_key)
        if not stored.public.enabled:
            self._health[stored.public.client_key] = ClientHealth("disabled")
            return []
        connection: _Connection | None = None
        try:
            connection = _Connection(stored, timeout_seconds=self.timeout_seconds)
            discovered = await asyncio.wait_for(connection.discover(), self.timeout_seconds)
            tools = await self.repository.sync_tools(stored, discovered)
            async with self._guard:
                self._connections[stored.public.client_key] = connection
            self._health[stored.public.client_key] = ClientHealth("connected")
            return tools
        except Exception as exc:
            if connection is not None:
                await connection.close(force=True)
            self._health[stored.public.client_key] = ClientHealth("error", type(exc).__name__)
            raise ApplicationError(
                "mcp_connection_failed",
                "MCP client connection or discovery failed.",
                status_code=502,
                details=[{"error_type": type(exc).__name__}],
            ) from exc

    async def disconnect(self, client_key: str) -> None:
        async with self._guard:
            connection = self._connections.pop(client_key, None)
        if connection is not None:
            await connection.close()

    async def call(
        self,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        actor: ActorContext,
        *,
        idempotency_key: str | None = None,
    ) -> McpCallResult:
        current = await self.repository.get_tool(descriptor.model_name)
        if current is None or not current.allowlisted or current.effect == "deny":
            raise ApplicationError(
                "mcp_tool_denied", "MCP tool is denied by policy.", status_code=403
            )
        validate_arguments(current.input_schema, arguments)
        actual = inject_trusted_actor(arguments, actor)
        if idempotency_key is not None:
            actual["idempotency_key"] = idempotency_key
        connection = self._connections.get(current.client_key)
        if connection is None:
            stored = await self.repository.get_client_by_key(current.client_key)
            if not stored.public.enabled:
                raise ApplicationError(
                    "mcp_client_disabled", "MCP client is disabled.", status_code=503
                )
            await self.reconnect(stored.public.client_id)
            connection = self._connections.get(current.client_key)
        if connection is None:
            raise ApplicationError(
                "mcp_client_unavailable", "MCP client is unavailable.", status_code=503
            )
        try:
            result = await asyncio.wait_for(
                connection.call(current.original_name, actual), self.timeout_seconds
            )
        except Exception as exc:
            self._health[current.client_key] = ClientHealth("error", type(exc).__name__)
            raise ApplicationError(
                "mcp_call_failed",
                "MCP tool call failed.",
                status_code=502,
                details=[{"error_type": type(exc).__name__}],
            ) from exc
        content = [item.model_dump(mode="json", exclude_none=True) for item in result.content]
        return McpCallResult(content=content, is_error=bool(result.isError))


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ApplicationError("invalid_tool_arguments", "Tool arguments must be an object.")
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    if isinstance(required, list):
        missing = [name for name in required if name not in arguments]
        if missing:
            raise ApplicationError(
                "invalid_tool_arguments",
                "Required tool arguments are missing.",
                details=[{"fields": missing}],
            )
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ApplicationError(
                "invalid_tool_arguments",
                "Unknown tool arguments were provided.",
                details=[{"fields": unknown}],
            )
    for name, value in arguments.items():
        field = properties.get(name)
        if not isinstance(field, dict) or not isinstance(field.get("type"), str):
            continue
        if not _matches_json_type(field["type"], value):
            raise ApplicationError(
                "invalid_tool_arguments", f"Tool argument '{name}' has an invalid type."
            )


def _matches_json_type(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True
