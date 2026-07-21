import asyncio
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_oa_service.config import MockOASettings
from mock_oa_service.database import Database
from mock_oa_service.domain import LeaveRequest, MockOAError
from mock_oa_service.mcp_server import create_mcp_server
from mock_oa_service.repository import LeaveRepository
from mock_oa_service.storage import initialize_mock_oa_data_layout

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str


class LeaveStatusUpdate(BaseModel):
    status: Literal["approved", "rejected"]


def _error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorBody(code=code, message=message, details=details), request_id=request_id
        ).model_dump(exclude_none=True),
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: MockOASettings | None = None) -> FastAPI:
    settings = settings or MockOASettings()
    repository_holder: list[LeaveRepository] = []

    def current_repository() -> LeaveRepository:
        if not repository_holder:
            raise RuntimeError("Mock OA repository is not ready")
        return repository_holder[0]

    mcp_server = create_mcp_server(current_repository)
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        try:
            layout = await asyncio.wait_for(
                asyncio.to_thread(initialize_mock_oa_data_layout, settings.data_dir),
                timeout=settings.startup_timeout_seconds,
            )
        except TimeoutError:
            raise RuntimeError("Mock OA startup timed out") from None
        app.state.data_layout = layout
        database = Database(layout.database, busy_timeout_ms=settings.sqlite_busy_timeout_ms)
        await asyncio.wait_for(database.migrate(), settings.startup_timeout_seconds)
        repository = LeaveRepository(
            database,
            default_entitlement_days=settings.default_annual_balance_days,
        )
        await repository.seed_employee(
            employee_id=settings.demo_employee_id,
            employee_no=settings.demo_employee_no,
            display_name=settings.demo_employee_name,
        )
        repository_holder.append(repository)
        app.state.database = database
        app.state.leave_repository = repository
        async with mcp_server.session_manager.run():
            app.state.ready = True
            try:
                yield
            finally:
                app.state.ready = False
                repository_holder.clear()

    app = FastAPI(title="Mock OA", version="0.1.0", lifespan=lifespan)
    app.state.ready = False

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid4())
        )
        started = time.perf_counter()
        if request.url.path.rstrip("/") == "/mcp":
            configured = _secret(settings.workhub_shared_secret)
            supplied = request.headers.get("X-WorkHub-Shared-Secret", "")
            if configured is None:
                return _error(
                    request,
                    503,
                    "mcp_auth_not_configured",
                    "Mock OA MCP authentication is not configured.",
                )
            if not supplied or not secrets.compare_digest(supplied, configured):
                return _error(request, 401, "mcp_auth_failed", "MCP authentication failed.")
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error")
            response = _error(request, 500, "internal_error", "An unexpected error occurred.")
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "Request completed",
            extra={
                "request_id": request.state.request_id,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return _error(request, 422, "validation_error", "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(request, exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(MockOAError)
    async def mock_oa_error(request: Request, exc: MockOAError) -> JSONResponse:
        return _error(request, exc.status_code, exc.code, exc.message)

    @app.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"], response_model=None)
    async def readiness(request: Request) -> JSONResponse | dict[str, str]:
        if request.app.state.ready:
            return {"status": "ready"}
        return _error(request, 503, "not_ready", "Service is not ready.")

    @app.patch(
        "/api/v1/leave-requests/{request_id}/status",
        response_model=LeaveRequest,
        tags=["demo-admin"],
    )
    async def update_leave_status(
        request_id: str, payload: LeaveStatusUpdate, request: Request
    ) -> LeaveRequest | JSONResponse:
        configured = _secret(settings.admin_token)
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if configured is None:
            return _error(
                request,
                503,
                "admin_auth_not_configured",
                "Demo admin authentication is not configured.",
            )
        if not supplied or not secrets.compare_digest(supplied, configured):
            return _error(request, 401, "admin_auth_failed", "Admin authentication failed.")
        return await current_repository().update_status(request_id, payload.status)

    app.mount("/", mcp_app, name="mcp")

    return app


def _secret(value: object) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    if not callable(getter):
        return None
    secret = str(getter()).strip()
    if not secret or secret.startswith("change-me"):
        return None
    return secret


app = create_app()
