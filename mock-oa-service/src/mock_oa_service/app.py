import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_oa_service.config import MockOASettings
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        try:
            app.state.data_layout = await asyncio.wait_for(
                asyncio.to_thread(initialize_mock_oa_data_layout, settings.data_dir),
                timeout=settings.startup_timeout_seconds,
            )
        except TimeoutError:
            raise RuntimeError("Mock OA startup timed out") from None
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            await asyncio.wait_for(asyncio.sleep(0), settings.shutdown_timeout_seconds)

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

    @app.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"], response_model=None)
    async def readiness(request: Request) -> JSONResponse | dict[str, str]:
        if request.app.state.ready:
            return {"status": "ready"}
        return _error(request, 503, "not_ready", "Service is not ready.")

    return app


app = create_app()
