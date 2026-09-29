from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Validated process configuration.

    Environment variables use the CHARTEROS_ prefix. No secrets are given source-code defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHARTEROS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    service_name: str = Field(default="charteros-api", min_length=1, max_length=64)
    log_level: LogLevel = "INFO"
    database_url: str = Field(
        default="postgresql+psycopg://charteros:charteros@localhost:5432/charteros",
        min_length=1,
    )
    api_host: str = Field(default="0.0.0.0", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    outbox_batch_size: int = Field(default=32, ge=1, le=500)
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    outbox_lease_seconds: int = Field(default=30, ge=1, le=3600)
    outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    outbox_backoff_base_seconds: int = Field(default=1, ge=1, le=3600)
    outbox_backoff_max_seconds: int = Field(default=300, ge=1, le=86400)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
