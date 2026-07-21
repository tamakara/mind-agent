from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from workhub.auth import AdminPrincipal
from workhub.domain import ChannelIdentity, Employee, Page
from workhub.employees.repository import EmployeeRepository, IdentityRepository


class EmployeeCreateRequest(BaseModel):
    employee_no: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    manager_employee_id: UUID | None = None
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class EmployeeUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    employee_no: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    department: str | None = Field(default=None, min_length=1, max_length=200)
    manager_employee_id: UUID | None = None
    timezone: str | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class IdentityBindRequest(BaseModel):
    employee_id: UUID
    expected_revision: int = Field(ge=0)


class IdentityUnbindRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class IdentityBindResponse(BaseModel):
    identity: ChannelIdentity
    session_id: UUID


def create_employee_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["employees"])

    @router.post("/employees", response_model=Employee, status_code=201)
    async def create_employee(payload: EmployeeCreateRequest, request: Request) -> Employee:
        repository: EmployeeRepository = request.app.state.employee_repository
        admin: AdminPrincipal = request.state.admin
        return await repository.create(
            **payload.model_dump(),
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.get("/employees", response_model=Page[Employee])
    async def list_employees(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> Page[Employee]:
        repository: EmployeeRepository = request.app.state.employee_repository
        return await repository.list(limit=limit, offset=offset)

    @router.get("/employees/{employee_id}", response_model=Employee)
    async def get_employee(employee_id: UUID, request: Request) -> Employee:
        repository: EmployeeRepository = request.app.state.employee_repository
        return await repository.get(employee_id)

    @router.patch("/employees/{employee_id}", response_model=Employee)
    async def update_employee(
        employee_id: UUID, payload: EmployeeUpdateRequest, request: Request
    ) -> Employee:
        repository: EmployeeRepository = request.app.state.employee_repository
        admin: AdminPrincipal = request.state.admin
        changes = payload.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        return await repository.update(
            employee_id,
            expected_revision=payload.expected_revision,
            changes=changes,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.get("/channel-identities", response_model=Page[ChannelIdentity])
    async def list_identities(
        request: Request,
        binding_status: Literal["unbound", "bound"] | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> Page[ChannelIdentity]:
        repository: IdentityRepository = request.app.state.identity_repository
        return await repository.list(binding_status=binding_status, limit=limit, offset=offset)

    @router.post("/channel-identities/{identity_id}/bind", response_model=IdentityBindResponse)
    async def bind_identity(
        identity_id: UUID, payload: IdentityBindRequest, request: Request
    ) -> IdentityBindResponse:
        repository: IdentityRepository = request.app.state.identity_repository
        admin: AdminPrincipal = request.state.admin
        identity, session_id = await repository.bind(
            identity_id,
            payload.employee_id,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )
        return IdentityBindResponse(identity=identity, session_id=session_id)

    @router.post("/channel-identities/{identity_id}/unbind", response_model=ChannelIdentity)
    async def unbind_identity(
        identity_id: UUID, payload: IdentityUnbindRequest, request: Request
    ) -> ChannelIdentity:
        repository: IdentityRepository = request.app.state.identity_repository
        admin: AdminPrincipal = request.state.admin
        return await repository.unbind(
            identity_id,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    return router
