import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, SchemaError
from mcp import ClientSessionGroup
from mcp.client.session_group import ClientSessionParameters, StreamableHttpParameters

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


class _Connection:
    def __init__(
        self, stored: StoredMcpClient, group: ClientSessionGroup, *, timeout_seconds: float
    ) -> None:
        self.stored = stored
        self.group = group
        self._timeout_seconds = timeout_seconds

    @classmethod
    async def connect(cls, stored: StoredMcpClient, *, timeout_seconds: float) -> "_Connection":
        group = ClientSessionGroup()
        await group.__aenter__()
        try:
            await group.connect_to_server(
                StreamableHttpParameters(
                    url=str(stored.public.url),
                    headers=stored.headers,
                    timeout=timedelta(seconds=timeout_seconds),
                    sse_read_timeout=timedelta(seconds=timeout_seconds),
                ),
                ClientSessionParameters(
                    read_timeout_seconds=timedelta(seconds=timeout_seconds)
                ),
            )
        except BaseException:
            await group.__aexit__(None, None, None)
            raise
        return cls(stored, group, timeout_seconds=timeout_seconds)

    async def discover(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in self.group.tools.values()
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self.group.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=self.group_timeout),
        )

    @property
    def group_timeout(self) -> float:
        return self._timeout_seconds

    async def close(self, *, force: bool = False) -> None:
        await self.group.__aexit__(None, None, None)



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
            connection = await _Connection.connect(stored, timeout_seconds=self.timeout_seconds)
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
    try:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
    except SchemaError as exc:
        raise ApplicationError(
            "invalid_tool_schema", "MCP tool schema is invalid.", status_code=502
        ) from exc
    if errors:
        details = [
            {
                "path": [str(part) for part in error.path],
                "message": error.message,
            }
            for error in errors[:10]
        ]
        raise ApplicationError(
            "invalid_tool_arguments",
            "Tool arguments do not match the MCP tool schema.",
            details=details,
        )
