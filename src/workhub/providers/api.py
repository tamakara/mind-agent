from typing import Literal

from fastapi import APIRouter, Request
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr

from workhub.auth import AdminPrincipal
from workhub.domain.providers import ModelSetting
from workhub.errors import ApplicationError
from workhub.providers.openai import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleEmbeddingProvider,
    test_chat_connection,
    test_embedding_connection,
)
from workhub.providers.repository import ModelSettingsRepository, ProviderKind


class ModelSettingRequest(BaseModel):
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class ConnectionTestResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ModelSettingDeleteRequest(BaseModel):
    expected_revision: int = Field(ge=0)


def create_provider_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/providers", tags=["providers"])

    @router.get("", response_model=list[ModelSetting])
    async def list_settings(request: Request) -> list[ModelSetting]:
        repository: ModelSettingsRepository = request.app.state.model_settings_repository
        return await repository.list()

    @router.put("/{provider_kind}", response_model=ModelSetting)
    async def put_setting(
        provider_kind: ProviderKind, payload: ModelSettingRequest, request: Request
    ) -> ModelSetting:
        repository: ModelSettingsRepository = request.app.state.model_settings_repository
        admin: AdminPrincipal = request.state.admin
        return await repository.upsert(
            provider_kind,
            base_url=str(payload.base_url).rstrip("/"),
            model=payload.model,
            api_key=payload.api_key.get_secret_value() if payload.api_key else None,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    @router.post("/{provider_kind}/test", response_model=ConnectionTestResponse)
    async def test_setting(provider_kind: ProviderKind, request: Request) -> ConnectionTestResponse:
        repository: ModelSettingsRepository = request.app.state.model_settings_repository
        setting = await repository.get(provider_kind)
        runtime_setting = await request.app.state.runtime_settings_repository.get()
        timeout = runtime_setting.provider_test_timeout_seconds
        try:
            if provider_kind == "chat":
                provider = OpenAICompatibleChatProvider(
                    base_url=setting.public.base_url,
                    api_key=setting.api_key,
                    model=setting.public.model,
                    timeout_seconds=timeout,
                )
                await test_chat_connection(provider, timeout)
            else:
                embeddings = OpenAICompatibleEmbeddingProvider(
                    base_url=setting.public.base_url,
                    api_key=setting.api_key,
                    model=setting.public.model,
                    timeout_seconds=timeout,
                )
                await test_embedding_connection(embeddings, timeout)
        except Exception as exc:
            raise ApplicationError(
                "provider_connection_failed",
                "Provider connection test failed.",
                status_code=502,
                details=[{"error_type": type(exc).__name__}],
            ) from exc
        return ConnectionTestResponse()

    @router.delete("/{provider_kind}", status_code=204)
    async def delete_setting(
        provider_kind: ProviderKind,
        payload: ModelSettingDeleteRequest,
        request: Request,
    ) -> None:
        repository: ModelSettingsRepository = request.app.state.model_settings_repository
        admin: AdminPrincipal = request.state.admin
        await repository.delete(
            provider_kind,
            expected_revision=payload.expected_revision,
            actor_id=admin.admin_user_id,
            request_id=request.state.request_id,
        )

    return router
