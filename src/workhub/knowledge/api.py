from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from workhub.auth import AdminPrincipal
from workhub.domain.knowledge import KnowledgeDocument, KnowledgeNode
from workhub.knowledge.service import KnowledgeService


class KnowledgeCreateRequest(BaseModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    node_type: Literal["directory", "document"]
    content: str = ""


class KnowledgeMoveRequest(BaseModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=0)


class KnowledgeSaveRequest(BaseModel):
    content: str
    expected_revision: int = Field(ge=0)


class KnowledgeDeleteRequest(BaseModel):
    expected_revision: int = Field(ge=0)


def create_knowledge_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

    @router.get("/nodes", response_model=list[KnowledgeNode])
    async def list_nodes(request: Request) -> list[KnowledgeNode]:
        service: KnowledgeService = request.app.state.knowledge_service
        return await service.list_nodes()

    @router.post("/nodes", response_model=KnowledgeNode, status_code=201)
    async def create_node(payload: KnowledgeCreateRequest, request: Request) -> KnowledgeNode:
        service: KnowledgeService = request.app.state.knowledge_service
        admin: AdminPrincipal = request.state.admin
        common = {
            "parent_id": payload.parent_id,
            "name": payload.name,
            "actor_id": admin.admin_user_id,
            "request_id": request.state.request_id,
        }
        if payload.node_type == "directory":
            return await service.create_directory(**common)
        return await service.create_document(**common, content=payload.content)

    @router.get("/documents/{node_id}", response_model=KnowledgeDocument)
    async def read_document(node_id: UUID, request: Request) -> KnowledgeDocument:
        service: KnowledgeService = request.app.state.knowledge_service
        return await service.read_document(node_id)

    @router.put("/documents/{node_id}", response_model=KnowledgeNode)
    async def save_document(
        node_id: UUID, payload: KnowledgeSaveRequest, request: Request
    ) -> KnowledgeNode:
        service: KnowledgeService = request.app.state.knowledge_service
        admin: AdminPrincipal = request.state.admin
        return await service.save_document(
            node_id,
            content=payload.content,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.patch("/nodes/{node_id}", response_model=KnowledgeNode)
    async def move_node(
        node_id: UUID, payload: KnowledgeMoveRequest, request: Request
    ) -> KnowledgeNode:
        service: KnowledgeService = request.app.state.knowledge_service
        admin: AdminPrincipal = request.state.admin
        return await service.move_node(
            node_id,
            parent_id=payload.parent_id,
            name=payload.name,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.delete("/nodes/{node_id}", status_code=204)
    async def delete_node(node_id: UUID, payload: KnowledgeDeleteRequest, request: Request) -> None:
        service: KnowledgeService = request.app.state.knowledge_service
        admin: AdminPrincipal = request.state.admin
        await service.delete_node(
            node_id,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.post("/nodes/{node_id}/retry", status_code=202)
    async def retry_index(node_id: UUID, request: Request) -> None:
        service: KnowledgeService = request.app.state.knowledge_service
        await service.retry(node_id)

    return router
