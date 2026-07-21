from pydantic import BaseModel, ConfigDict, Field


class RuntimeSetting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_test_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    agent_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    agent_tool_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    agent_max_iterations: int = Field(default=8, ge=1, le=32)
    agent_context_token_budget: int = Field(default=8_000, ge=512, le=1_000_000)
    mcp_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    pending_action_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    feishu_reconnect_attempts: int = Field(default=3, ge=0, le=20)
    feishu_reconnect_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    feishu_api_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    revision: int = Field(default=0, ge=0)
    created_at: str | None = None
    updated_at: str | None = None


class FeishuSetting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app_id: str
    app_secret_configured: bool
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str


class StoredFeishuSetting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    public: FeishuSetting
    app_secret: str
