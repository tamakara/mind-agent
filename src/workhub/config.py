from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkHubSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("~/.workhub")
    static_dir: Path = Path("frontend/dist")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    startup_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    sqlite_busy_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    log_level: str = "INFO"
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    admin_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=60, le=7 * 24 * 60 * 60)
    admin_cookie_secure: bool = False
    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    mock_oa_mcp_url: str | None = None
    mock_oa_shared_secret: SecretStr | None = None

    @field_validator("data_dir", "static_dir")
    @classmethod
    def expand_path(cls, value: Path) -> Path:
        return value.expanduser()
