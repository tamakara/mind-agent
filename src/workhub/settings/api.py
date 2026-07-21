import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, SecretStr

from workhub.auth import AdminPrincipal
from workhub.errors import ApplicationError
from workhub.settings.models import FeishuSetting, RuntimeSetting
from workhub.settings.repository import FeishuSettingsRepository, RuntimeSettingsRepository


class RuntimeSettingRequest(BaseModel):
    provider_test_timeout_seconds: float = Field(gt=0, le=60)
    agent_timeout_seconds: float = Field(gt=0, le=300)
    agent_tool_timeout_seconds: float = Field(gt=0, le=120)
    agent_max_iterations: int = Field(ge=1, le=32)
    agent_context_token_budget: int = Field(ge=512, le=1_000_000)
    mcp_timeout_seconds: float = Field(gt=0, le=120)
    pending_action_ttl_seconds: int = Field(ge=60, le=3_600)
    feishu_reconnect_attempts: int = Field(ge=0, le=20)
    feishu_reconnect_delay_seconds: float = Field(ge=0, le=60)
    feishu_api_timeout_seconds: float = Field(gt=0, le=60)
    expected_revision: int | None = Field(default=None, ge=0)


class FeishuSettingRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: SecretStr | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class FeishuDeleteRequest(BaseModel):
    expected_revision: int = Field(ge=0)


async def _apply_runtime(request: Request, setting: RuntimeSetting) -> None:
    callback = getattr(request.app.state, "apply_runtime_settings", None)
    if callback is not None:
        try:
            await callback(setting)
        except Exception as exc:
            raise ApplicationError(
                "runtime_apply_failed",
                "Runtime settings were saved but could not be applied.",
                status_code=503,
                details=[{"error_type": type(exc).__name__}],
            ) from exc


async def _apply_feishu(request: Request) -> None:
    callback = getattr(request.app.state, "apply_feishu_settings", None)
    if callback is not None:
        try:
            await callback()
        except Exception as exc:
            raise ApplicationError(
                "feishu_apply_failed",
                "Feishu settings were saved but could not be applied.",
                status_code=503,
                details=[{"error_type": type(exc).__name__}],
            ) from exc


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

    @router.get("/runtime", response_model=RuntimeSetting)
    async def get_runtime(request: Request) -> RuntimeSetting:
        repository: RuntimeSettingsRepository = request.app.state.runtime_settings_repository
        return await repository.get()

    @router.put("/runtime", response_model=RuntimeSetting)
    async def put_runtime(payload: RuntimeSettingRequest, request: Request) -> RuntimeSetting:
        repository: RuntimeSettingsRepository = request.app.state.runtime_settings_repository
        admin: AdminPrincipal = request.state.admin
        setting = RuntimeSetting(**payload.model_dump(exclude={"expected_revision"}))
        saved = await repository.upsert(
            setting,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )
        await _apply_runtime(request, saved)
        return saved

    @router.get("/feishu", response_model=FeishuSetting | None)
    async def get_feishu(request: Request) -> FeishuSetting | None:
        repository: FeishuSettingsRepository = request.app.state.feishu_settings_repository
        stored = await repository.get()
        return stored.public if stored is not None else None

    @router.put("/feishu", response_model=FeishuSetting)
    async def put_feishu(payload: FeishuSettingRequest, request: Request) -> FeishuSetting:
        repository: FeishuSettingsRepository = request.app.state.feishu_settings_repository
        admin: AdminPrincipal = request.state.admin
        stored = await repository.upsert(
            app_id=payload.app_id.strip(),
            app_secret=payload.app_secret.get_secret_value().strip()
            if payload.app_secret
            else None,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )
        await _apply_feishu(request)
        return stored.public

    @router.delete("/feishu", status_code=204)
    async def delete_feishu(payload: FeishuDeleteRequest, request: Request) -> None:
        repository: FeishuSettingsRepository = request.app.state.feishu_settings_repository
        admin: AdminPrincipal = request.state.admin
        await repository.delete(
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )
        await _apply_feishu(request)

    @router.post("/feishu/test")
    async def test_feishu(request: Request) -> dict[str, str]:
        repository: FeishuSettingsRepository = request.app.state.feishu_settings_repository
        if await repository.get() is None:
            raise ApplicationError(
                "feishu_not_configured", "Feishu is not configured.", status_code=404
            )
        await _apply_feishu(request)
        await asyncio.sleep(0.2)
        gateway = request.app.state.feishu_gateway
        status = gateway.status() if gateway is not None else None
        if status is None or status.state == "degraded":
            raise ApplicationError(
                "feishu_connection_failed",
                "Feishu connection test failed.",
                status_code=502,
                details=[{"state": status.state if status is not None else "disabled"}],
            )
        return {"status": "ok"}

    return router
