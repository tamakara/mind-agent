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
from pydantic import SecretStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from workhub.audit import AuditWriter
from workhub.auth import AuthService
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
        app.state.employee_repository = EmployeeRepository(database, audit)
        identities = IdentityRepository(database, audit)
        app.state.identity_repository = identities
        model_settings = ModelSettingsRepository(database, audit)
        app.state.model_settings_repository = model_settings
        await _bootstrap_provider_settings(model_settings, settings)
        scroll_repository = ScrollRepository(database)
        recall_service = RecallService(database)
        app.state.scroll_repository = scroll_repository
        app.state.recall_service = recall_service
        mcp_repository = McpRepository(database, audit)
        await _bootstrap_mock_oa_client(mcp_repository, settings)
        mcp_manager = MCPManager(mcp_repository, timeout_seconds=settings.mcp_timeout_seconds)
        pending_actions = PendingActionRepository(
            database, audit, ttl_seconds=settings.pending_action_ttl_seconds
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
                timeout_seconds=settings.agent_timeout_seconds,
            )

        async def embedding_provider_factory() -> EmbeddingProvider:
            stored = await model_settings.get("embedding")
            from workhub.providers import OpenAICompatibleEmbeddingProvider

            return OpenAICompatibleEmbeddingProvider(
                base_url=stored.public.base_url,
                api_key=stored.api_key,
                model=stored.public.model,
                timeout_seconds=settings.provider_test_timeout_seconds,
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

        feishu_gateway: FeishuGateway | None = None
        if _feishu_configured(settings):
            assert settings.feishu_app_id is not None
            assert settings.feishu_app_secret is not None
            app_secret = settings.feishu_app_secret.get_secret_value()
            transport = OfficialFeishuTransport(
                settings.feishu_app_id,
                app_secret,
                timeout_seconds=settings.feishu_api_timeout_seconds,
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
                max_iterations=settings.agent_max_iterations,
                total_timeout_seconds=settings.agent_timeout_seconds,
                tool_timeout_seconds=settings.agent_tool_timeout_seconds,
                context_token_budget=settings.agent_context_token_budget,
            )
            app.state.agent_runtime = runtime
            confirmations = ConfirmationService(
                pending_actions,
                mcp_repository,
                mcp_manager,
                scroll_repository,
                transport,
            )
            await confirmations.recover()
            app.state.confirmation_service = confirmations
            router = FeishuEventRouter(
                identities,
                transport,
                audit,
                message_handler=runtime,
                card_handler=confirmations,
            )
            feishu_gateway = FeishuGateway(
                settings.feishu_app_id,
                app_secret,
                router,
                reconnect_attempts=settings.feishu_reconnect_attempts,
                reconnect_delay_seconds=settings.feishu_reconnect_delay_seconds,
            )
            await feishu_gateway.start()
        app.state.feishu_gateway = feishu_gateway
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
        if gateway is None:
            return {"state": "disabled"}
        status = gateway.status()
        return {
            "state": status.state,
            "reconnect_attempt": status.reconnect_attempt,
            "last_error": status.last_error,
            "updated_at": status.updated_at,
        }

    app.include_router(create_auth_router())
    app.include_router(create_employee_router())
    app.include_router(create_provider_router())
    app.include_router(create_knowledge_router())
    app.include_router(create_mcp_router())

    if settings.static_dir.is_dir() and (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(settings.static_dir), name="admin")
    else:
        logger.info("Administration frontend is not built; static serving is disabled")

    return app


def _feishu_configured(settings: WorkHubSettings) -> bool:
    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        return False
    app_id = settings.feishu_app_id.strip()
    secret = settings.feishu_app_secret.get_secret_value().strip()
    return bool(
        app_id
        and secret
        and not app_id.startswith("change-me")
        and not secret.startswith("change-me")
    )


async def _bootstrap_provider_settings(
    repository: ModelSettingsRepository, settings: WorkHubSettings
) -> None:
    if _initial_provider_configured(
        settings.chat_base_url, settings.chat_api_key, settings.chat_model
    ):
        assert settings.chat_base_url is not None
        assert settings.chat_api_key is not None
        assert settings.chat_model is not None
        await repository.bootstrap(
            "chat",
            base_url=settings.chat_base_url,
            api_key=settings.chat_api_key.get_secret_value(),
            model=settings.chat_model,
        )
    if _initial_provider_configured(
        settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model
    ):
        assert settings.embedding_base_url is not None
        assert settings.embedding_api_key is not None
        assert settings.embedding_model is not None
        await repository.bootstrap(
            "embedding",
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            model=settings.embedding_model,
        )


def _initial_provider_configured(
    base_url: str | None, api_key: SecretStr | None, model: str | None
) -> bool:
    if base_url is None or api_key is None or model is None:
        return False
    secret = api_key.get_secret_value()
    return bool(
        base_url.strip()
        and model.strip()
        and secret.strip()
        and not model.startswith("change-me")
        and not secret.startswith("change-me")
    )


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
