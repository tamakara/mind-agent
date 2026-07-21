import asyncio
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from workhub.audit import AuditWriter
from workhub.auth import AuthService
from workhub.auth.api import CSRF_COOKIE, SESSION_COOKIE, create_auth_router
from workhub.config import WorkHubSettings
from workhub.errors import ApplicationError, ErrorBody, ErrorResponse
from workhub.observability import configure_logging
from workhub.static import SpaStaticFiles
from workhub.storage import Database, initialize_workhub_data_layout

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PUBLIC_API_PATHS = frozenset({"/api/v1/auth/bootstrap-status", "/api/v1/auth/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers={"X-Request-ID": request_id},
    )


def _origin_allowed(request: Request, settings: WorkHubSettings) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return False
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    normalized = f"{parsed.scheme}://{parsed.netloc}"
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    configured = {item.strip().rstrip("/") for item in settings.allowed_origins.split(",")}
    return normalized == request_origin or normalized in configured


async def _admin_guard(request: Request, settings: WorkHubSettings) -> JSONResponse | None:
    if request.url.path != "/api/v1" and not request.url.path.startswith("/api/v1/"):
        return None
    if request.method in UNSAFE_METHODS and not _origin_allowed(request, settings):
        return _error_response(
            request,
            status_code=403,
            code="origin_forbidden",
            message="Request origin is not allowed.",
        )
    if request.url.path in PUBLIC_API_PATHS:
        return None
    service: AuthService = request.app.state.auth_service
    principal = await service.authenticate(request.cookies.get(SESSION_COOKIE))
    if principal is None:
        return _error_response(
            request,
            status_code=401,
            code="authentication_required",
            message="Administrator authentication is required.",
        )
    request.state.admin = principal
    if request.method in UNSAFE_METHODS:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get("X-CSRF-Token")
        if (
            not cookie_token
            or not header_token
            or not secrets.compare_digest(cookie_token, header_token)
            or not service.verify_csrf(principal, header_token)
        ):
            return _error_response(
                request,
                status_code=403,
                code="csrf_failed",
                message="CSRF validation failed.",
            )
    return None


def create_app(settings: WorkHubSettings | None = None) -> FastAPI:
    settings = settings or WorkHubSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.ready = False
        try:
            layout = await asyncio.wait_for(
                asyncio.to_thread(initialize_workhub_data_layout, settings.data_dir),
                timeout=settings.startup_timeout_seconds,
            )
        except TimeoutError:
            logger.exception("WorkHub startup timed out")
            raise RuntimeError("WorkHub startup timed out") from None
        app.state.data_layout = layout
        database = Database(
            layout.database,
            busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        )
        await asyncio.wait_for(database.migrate(), timeout=settings.startup_timeout_seconds)
        audit = AuditWriter(database)
        auth_service = AuthService(
            database,
            audit,
            session_ttl_seconds=settings.admin_session_ttl_seconds,
            login_window_seconds=settings.admin_login_window_seconds,
            login_max_attempts=settings.admin_login_max_attempts,
            session_secret=(
                settings.session_secret.get_secret_value()
                if settings.session_secret is not None
                else None
            ),
        )
        bootstrap_password = (
            settings.bootstrap_admin_password.get_secret_value()
            if settings.bootstrap_admin_password is not None
            else None
        )
        await asyncio.wait_for(
            auth_service.bootstrap(settings.bootstrap_admin_username, bootstrap_password),
            timeout=settings.startup_timeout_seconds,
        )
        app.state.database = database
        app.state.audit = audit
        app.state.auth_service = auth_service
        app.state.ready = True
        logger.info("WorkHub started")
        try:
            yield
        finally:
            app.state.ready = False
            try:
                await asyncio.wait_for(asyncio.sleep(0), settings.shutdown_timeout_seconds)
            except TimeoutError:
                logger.error("WorkHub shutdown timed out")
            logger.info("WorkHub stopped")

    app = FastAPI(title="WorkHub", version="0.1.0", lifespan=lifespan)
    app.state.ready = False
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid4())
        )
        started = time.perf_counter()
        try:
            blocked = await _admin_guard(request, settings)
            response = blocked if blocked is not None else await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request error",
                extra={"request_id": request.state.request_id},
            )
            response = _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="An unexpected error occurred.",
            )
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "Request completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Request validation failed.",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(
            request,
            status_code=exc.status_code,
            code="http_error",
            message=message,
        )

    @app.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"], response_model=None)
    async def readiness(request: Request) -> JSONResponse | dict[str, str]:
        if request.app.state.ready:
            return {"status": "ready"}
        return _error_response(
            request,
            status_code=503,
            code="not_ready",
            message="Service is not ready.",
        )

    app.include_router(create_auth_router())

    if settings.static_dir.is_dir() and (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(settings.static_dir), name="admin")
    else:
        logger.info("Administration frontend is not built; static serving is disabled")

    return app


app = create_app()
