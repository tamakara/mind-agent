from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkHubSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("~/.workhub")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    startup_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    log_level: str = "INFO"

    @field_validator("data_dir")
    @classmethod
    def expand_data_dir(cls, value: Path) -> Path:
        return value.expanduser()
