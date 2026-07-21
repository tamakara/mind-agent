from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MockOASettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOCK_OA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("~/.workhub-mock-oa")
    host: str = "127.0.0.1"
    port: int = Field(default=8001, ge=1, le=65535)
    startup_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    log_level: str = "INFO"
    sqlite_busy_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    admin_token: SecretStr | None = None
    workhub_shared_secret: SecretStr | None = None
    default_annual_balance_days: float = Field(default=10, gt=0, le=365)
    demo_employee_id: str = "00000000-0000-4000-8000-000000000001"
    demo_employee_no: str = "E10001"
    demo_employee_name: str = "演示员工"

    @field_validator("data_dir")
    @classmethod
    def expand_data_dir(cls, value: Path) -> Path:
        return value.expanduser()
