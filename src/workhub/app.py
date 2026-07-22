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
from workhub.auth import AuthService, get_or_create_instance_secret
from workhub.auth.api import CSRF_COOKIE, SESSION_COOKIE, create_auth_router
from workhub.config import WorkHubSettings
from workhub.confirmations import ConfirmationService, PendingActionRepository
from workhub.context import RecallService, ScrollBuilder, ScrollRepository
from workhub.employees import EmployeeRepository, IdentityRepository
from workhub.employees.api import create_employee_router
from workhub.errors import ApplicationError, ErrorBody, ErrorResponse
from workhub.feishu import FeishuEventRouter, FeishuGateway, OfficialFeishuTransport
from workhub.knowledge import (
    KnowledgeIndexer,
    KnowledgeReconciler,
    KnowledgeRepository,
    KnowledgeRuntimeToolProvider,
    KnowledgeService,
    create_knowledge_router,
)
from workhub.knowledge.vector_store import KnowledgeVectorStore
from workhub.management import create_management_router
from workhub.mcp import MCPManager, McpRepository, create_mcp_router
from workhub.mcp.runtime import McpRuntimeToolProvider
from workhub.observability import configure_logging
from workhub.providers import (
    ModelSettingsRepository,
    OpenAICompatibleChatProvider,
    create_provider_router,
)
from workhub.providers.openai import ChatProvider, EmbeddingProvider
from workhub.runtime import (
    AgentRuntime,
    CompositeRuntimeToolProvider,
    CoreRuntimeToolProvider,
)
from workhub.settings import (
    FeishuSettingsRepository,
    RuntimeSetting,
    RuntimeSettingsRepository,
    StoredFeishuSetting,
    create_settings_router,
)
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
        session_secret = await get_or_create_instance_secret(database, "admin_session_hmac")
        auth_service = AuthService(
            database,
            audit,
            session_ttl_seconds=settings.admin_session_ttl_seconds,
            login_window_seconds=settings.admin_login_window_seconds,
            login_max_attempts=settings.admin_login_max_attempts,
            session_secret=session_secret,
        )
        bootstrap_password = (
            settings.admin_password.get_secret_value()
            if settings.admin_password is not None
            else None
        )
        await asyncio.wait_for(
            auth_service.bootstrap(settings.admin_username, bootstrap_password),
            timeout=settings.startup_timeout_seconds,
        )
        app.state.database = database
        app.state.audit = audit
        app.state.auth_service = auth_service
        app.state.employee_repository = EmployeeRepository(database, audit)
        identities = IdentityRepository(database, audit)
        app.state.identity_repository = identities
        model_settings = ModelSettingsRepository(database, audit)
        app.state.model_settings_repository = model_settings
        runtime_settings_repository = RuntimeSettingsRepository(database, audit)
        feishu_settings_repository = FeishuSettingsRepository(database, audit)
        runtime_setting = await runtime_settings_repository.get()
        app.state.runtime_settings_repository = runtime_settings_repository
        app.state.feishu_settings_repository = feishu_settings_repository
        app.state.runtime_setting = runtime_setting
        scroll_repository = ScrollRepository(database)
        recall_service = RecallService(database)
        app.state.scroll_repository = scroll_repository
        app.state.recall_service = recall_service
        mcp_repository = McpRepository(database, audit)
        await _bootstrap_mock_oa_client(mcp_repository, settings)
        mcp_manager = MCPManager(
            mcp_repository, timeout_seconds=runtime_setting.mcp_timeout_seconds
        )
        pending_actions = PendingActionRepository(
            database, audit, ttl_seconds=runtime_setting.pending_action_ttl_seconds
        )
        app.state.mcp_repository = mcp_repository
        app.state.mcp_manager = mcp_manager
        app.state.pending_action_repository = pending_actions
        await mcp_manager.start()

        async def chat_provider_factory() -> ChatProvider:
            stored = await model_settings.get("chat")
            return OpenAICompatibleChatProvider(
                base_url=stored.public.base_url,
                api_key=stored.api_key,
                model=stored.public.model,
                timeout_seconds=runtime_setting.agent_timeout_seconds,
            )

        async def embedding_provider_factory() -> EmbeddingProvider:
            stored = await model_settings.get("embedding")
            from workhub.providers import OpenAICompatibleEmbeddingProvider

            return OpenAICompatibleEmbeddingProvider(
                base_url=stored.public.base_url,
                api_key=stored.api_key,
                model=stored.public.model,
                timeout_seconds=runtime_setting.provider_test_timeout_seconds,
            )

        vector_store = await asyncio.to_thread(KnowledgeVectorStore, layout.knowledge_index)
        knowledge_repository = KnowledgeRepository(database, layout, audit)
        knowledge_indexer = KnowledgeIndexer(
            database,
            layout,
            vector_store,
            embedding_provider_factory,
        )
        knowledge_service = KnowledgeService(
            database,
            knowledge_repository,
            knowledge_indexer,
            vector_store,
            embedding_provider_factory,
        )
        app.state.knowledge_service = knowledge_service
        reconciler = KnowledgeReconciler(
            database,
            layout,
            knowledge_repository,
            knowledge_indexer,
            vector_store,
            audit,
        )
        await reconciler.run_once()
        await knowledge_indexer.start()

        app.state.feishu_gateway = None
        app.state.agent_runtime = None
        app.state.confirmation_service = None
        app.state.feishu_last_apply_error = None

        async def build_feishu(
            stored: StoredFeishuSetting,
        ) -> tuple[FeishuGateway, AgentRuntime, ConfirmationService]:
            transport = OfficialFeishuTransport(
                stored.public.app_id,
                stored.app_secret,
                timeout_seconds=runtime_setting.feishu_api_timeout_seconds,
            )
            runtime = AgentRuntime(
                scroll_repository,
                ScrollBuilder(scroll_repository),
                chat_provider_factory,
                CompositeRuntimeToolProvider(
                    CoreRuntimeToolProvider(recall_service),
                    KnowledgeRuntimeToolProvider(knowledge_service),
                    McpRuntimeToolProvider(mcp_repository, mcp_manager, pending_actions, transport),
                ),
                transport,
                max_iterations=runtime_setting.agent_max_iterations,
                total_timeout_seconds=runtime_setting.agent_timeout_seconds,
                tool_timeout_seconds=runtime_setting.agent_tool_timeout_seconds,
                context_token_budget=runtime_setting.agent_context_token_budget,
            )
            confirmations = ConfirmationService(
                pending_actions,
                mcp_repository,
                mcp_manager,
                scroll_repository,
                transport,
            )
            await confirmations.recover()
            router = FeishuEventRouter(
                identities,
                transport,
                audit,
                message_handler=runtime,
                card_handler=confirmations,
            )
            gateway = FeishuGateway(
                stored.public.app_id,
                stored.app_secret,
                router,
                reconnect_attempts=runtime_setting.feishu_reconnect_attempts,
                reconnect_delay_seconds=runtime_setting.feishu_reconnect_delay_seconds,
            )
            return gateway, runtime, confirmations

        async def apply_feishu_settings() -> None:
            stored = await feishu_settings_repository.get()
            old_gateway: FeishuGateway | None = app.state.feishu_gateway
            if stored is None:
                if old_gateway is not None:
                    await old_gateway.close()
                app.state.feishu_gateway = None
                app.state.agent_runtime = None
                app.state.confirmation_service = None
                return
            gateway, runtime, confirmations = await build_feishu(stored)
            await gateway.start()
            if old_gateway is not None:
                await old_gateway.close()
            app.state.feishu_gateway = gateway
            app.state.agent_runtime = runtime
            app.state.confirmation_service = confirmations
            app.state.feishu_last_apply_error = None

        async def apply_runtime_settings(value: RuntimeSetting) -> None:
            nonlocal runtime_setting
            runtime_setting = value
            app.state.runtime_setting = value
            mcp_manager.timeout_seconds = value.mcp_timeout_seconds
            pending_actions.ttl_seconds = value.pending_action_ttl_seconds
            runtime: AgentRuntime | None = app.state.agent_runtime
            if runtime is not None:
                runtime.max_iterations = value.agent_max_iterations
                runtime.total_timeout_seconds = value.agent_timeout_seconds
                runtime.tool_timeout_seconds = value.agent_tool_timeout_seconds
                runtime.context_token_budget = value.agent_context_token_budget
            await apply_feishu_settings()

        app.state.apply_feishu_settings = apply_feishu_settings
        app.state.apply_runtime_settings = apply_runtime_settings
        await apply_feishu_settings()
        app.state.ready = True
        logger.info("WorkHub started")
        try:
            yield
        finally:
            app.state.ready = False
            try:
                await asyncio.wait_for(knowledge_indexer.close(), settings.shutdown_timeout_seconds)
            except TimeoutError:
                logger.error("Knowledge indexer shutdown timed out")
            feishu_gateway: FeishuGateway | None = app.state.feishu_gateway
            if feishu_gateway is not None:
                try:
                    await asyncio.wait_for(
                        feishu_gateway.close(), settings.shutdown_timeout_seconds
                    )
                except TimeoutError:
                    logger.error("Feishu shutdown timed out")
            try:
                await asyncio.wait_for(mcp_manager.close(), settings.shutdown_timeout_seconds)
            except TimeoutError:
                logger.error("MCP shutdown timed out")
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

    @app.get("/api/v1/feishu/status", tags=["feishu"])
    async def feishu_status(request: Request) -> dict[str, object]:
        gateway: FeishuGateway | None = request.app.state.feishu_gateway
        stored = await request.app.state.feishu_settings_repository.get()
        if gateway is None:
            return {
                "state": "disabled",
                "configured": stored is not None,
                "last_apply_error": request.app.state.feishu_last_apply_error,
            }
        status = gateway.status()
        return {
            "state": status.state,
            "configured": stored is not None,
            "reconnect_attempt": status.reconnect_attempt,
            "last_error": status.last_error,
            "updated_at": status.updated_at,
            "last_apply_error": request.app.state.feishu_last_apply_error,
        }

    app.include_router(create_auth_router())
    app.include_router(create_employee_router())
    app.include_router(create_provider_router())
    app.include_router(create_knowledge_router())
    app.include_router(create_mcp_router())
    app.include_router(create_management_router())
    app.include_router(create_settings_router())

    if settings.static_dir.is_dir() and (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(settings.static_dir), name="admin")
    else:
        logger.info("Administration frontend is not built; static serving is disabled")

    return app


async def _bootstrap_mock_oa_client(repository: McpRepository, settings: WorkHubSettings) -> None:
    if settings.mock_oa_mcp_url is None or settings.mock_oa_shared_secret is None:
        return
    url = settings.mock_oa_mcp_url.strip()
    secret = settings.mock_oa_shared_secret.get_secret_value().strip()
    if (
        not url
        or not secret
        or not url.startswith(("http://", "https://"))
        or secret.startswith("change-me")
    ):
        return
    await repository.bootstrap_client(
        client_key="mock_oa",
        name="Mock OA",
        url=url,
        headers={"X-WorkHub-Shared-Secret": secret},
    )


app = create_app()
