from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator

from workhub.auth import AdminPrincipal
from workhub.domain import McpClient, McpToolEffect, ToolDescriptor
from workhub.mcp.manager import MCPManager
from workhub.mcp.repository import CLIENT_KEY, McpRepository


class McpClientRequest(BaseModel):
    client_key: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=100)
    url: AnyHttpUrl
    headers: dict[str, SecretStr] | None = None
    enabled: bool = False
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("client_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not CLIENT_KEY.fullmatch(value):
            raise ValueError("client_key must use lowercase letters, digits, and underscores")
        return value


class McpClientDeleteRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class McpToolSettingRequest(BaseModel):
    allowlisted: bool
    effect: McpToolEffect
    expected_revision: int = Field(ge=0)


class McpConnectionResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tool_count: int = Field(ge=0)


def create_mcp_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

    @router.get("/clients", response_model=list[McpClient])
    async def list_clients(request: Request) -> list[McpClient]:
        repository: McpRepository = request.app.state.mcp_repository
        return await repository.list_clients()

    @router.post("/clients", response_model=McpClient, status_code=201)
    async def create_client(payload: McpClientRequest, request: Request) -> McpClient:
        return await _save_client(None, payload, request)

    @router.put("/clients/{client_id}", response_model=McpClient)
    async def update_client(
        client_id: UUID, payload: McpClientRequest, request: Request
    ) -> McpClient:
        return await _save_client(client_id, payload, request)

    @router.delete("/clients/{client_id}", status_code=204)
    async def delete_client(
        client_id: UUID, payload: McpClientDeleteRequest, request: Request
    ) -> None:
        repository: McpRepository = request.app.state.mcp_repository
        manager: MCPManager = request.app.state.mcp_manager
        stored = await repository.get_client(client_id)
        await manager.disconnect(stored.public.client_key)
        admin: AdminPrincipal = request.state.admin
        await repository.delete_client(
            client_id,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.post("/clients/{client_id}/connect", response_model=McpConnectionResponse)
    async def connect_client(client_id: UUID, request: Request) -> McpConnectionResponse:
        manager: MCPManager = request.app.state.mcp_manager
        tools = await manager.reconnect(client_id)
        return McpConnectionResponse(tool_count=len(tools))

    @router.get("/tools", response_model=list[ToolDescriptor])
    async def list_tools(request: Request) -> list[ToolDescriptor]:
        repository: McpRepository = request.app.state.mcp_repository
        return await repository.list_tools()

    @router.put("/tools/{tool_id}/setting", response_model=ToolDescriptor)
    async def update_tool(
        tool_id: UUID, payload: McpToolSettingRequest, request: Request
    ) -> ToolDescriptor:
        repository: McpRepository = request.app.state.mcp_repository
        admin: AdminPrincipal = request.state.admin
        return await repository.update_tool_setting(
            tool_id,
            allowlisted=payload.allowlisted,
            effect=payload.effect,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.get("/status")
    async def status(request: Request) -> dict[str, dict[str, str | None]]:
        manager: MCPManager = request.app.state.mcp_manager
        return {
            key: {"state": value.state, "error_code": value.error_code}
            for key, value in manager.health().items()
        }

    return router


async def _save_client(
    client_id: UUID | None, payload: McpClientRequest, request: Request
) -> McpClient:
    repository: McpRepository = request.app.state.mcp_repository
    manager: MCPManager = request.app.state.mcp_manager
    admin: AdminPrincipal = request.state.admin
    old_key = None
    if client_id is not None:
        old_key = (await repository.get_client(client_id)).public.client_key
    result = await repository.upsert_client(
        client_id=client_id,
        client_key=payload.client_key,
        name=payload.name,
        url=str(payload.url),
        headers=(
            {key: value.get_secret_value() for key, value in payload.headers.items()}
            if payload.headers is not None
            else None
        ),
        enabled=payload.enabled,
        expected_revision=payload.expected_revision,
        actor_id=admin.admin_user_id,
        request_id=request.state.request_id,
    )
    if old_key is not None and old_key != result.client_key:
        await manager.disconnect(old_key)
    if payload.enabled:
        await manager.reconnect(result.client_id)
    else:
        await manager.disconnect(result.client_key)
    return result
