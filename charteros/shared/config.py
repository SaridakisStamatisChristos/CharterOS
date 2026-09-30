from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_INSECURE_DATABASE_PASSWORDS = frozenset(
    {
        "charteros",
        "change-me",
        "changeme",
        "password",
        "replace-with-random-local-password",
    }
)


class Settings(BaseSettings):
    """Validated process configuration.

    Environment variables use the CHARTEROS_ prefix. Database credentials are always explicit;
    staging and production additionally require a complete external OIDC configuration.
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
    database_url: str = Field(min_length=1, repr=False)
    database_runtime_role: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$",
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60.0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86_400)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    api_max_json_depth: int = Field(default=32, ge=4, le=128)
    api_request_body_read_timeout_seconds: float = Field(default=10.0, gt=0, le=60.0)
    trusted_ingress_rate_limit_enforced: bool = False
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    api_matching_requests_per_window: int = Field(default=300, ge=1, le=10_000)
    api_repositioning_requests_per_window: int = Field(default=60, ge=1, le=10_000)
    api_evidence_requests_per_window: int = Field(default=120, ge=1, le=10_000)
    api_graph_requests_per_window: int = Field(default=600, ge=1, le=10_000)
    api_rate_limit_window_retention_seconds: int = Field(default=7200, ge=60, le=604_800)
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwks_url: str | None = None
    auth_allowed_algorithms: str = "RS256,ES256"
    auth_jwks_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    auth_jwks_max_keys: int = Field(default=64, ge=1, le=512)
    auth_jwks_max_document_bytes: int = Field(default=262_144, ge=4096, le=4_194_304)
    auth_jwks_refresh_min_interval_seconds: float = Field(default=5.0, ge=0.1, le=300.0)
    auth_jwks_unknown_key_ttl_seconds: float = Field(default=5.0, ge=1.0, le=3600.0)
    auth_jwks_negative_cache_max_keys: int = Field(default=128, ge=1, le=2048)
    auth_http_timeout_seconds: float = Field(default=2.0, gt=0, le=30.0)
    auth_jwt_leeway_seconds: float = Field(default=0.0, ge=0, le=300.0)
    outbox_batch_size: int = Field(default=32, ge=1, le=500)
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    outbox_lease_seconds: int = Field(default=30, ge=1, le=3600)
    outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    outbox_backoff_base_seconds: int = Field(default=1, ge=1, le=3600)
    outbox_backoff_max_seconds: int = Field(default=300, ge=1, le=86400)
    idempotency_retention_days: int = Field(default=90, ge=1, le=365)
    idempotency_cleanup_batch_size: int = Field(default=1000, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_deployment_security(self) -> Settings:
        try:
            database = make_url(self.database_url)
        except ArgumentError as exc:
            raise ValueError("CHARTEROS_DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if not database.drivername.startswith("postgresql"):
            raise ValueError("CHARTEROS_DATABASE_URL must use PostgreSQL")

        auth_values = (self.auth_issuer, self.auth_audience, self.auth_jwks_url)
        if any(value is not None for value in auth_values) and not all(
            value is not None for value in auth_values
        ):
            raise ValueError(
                "OIDC configuration is atomic: issuer, audience, and JWKS URL must be set together"
            )

        if self.api_rate_limit_window_retention_seconds < self.api_rate_limit_window_seconds * 2:
            raise ValueError(
                "rate-limit window retention must cover at least two complete rate windows"
            )

        if self.environment in {"staging", "production"}:
            if not all(value is not None for value in auth_values):
                raise ValueError(
                    "staging/production require CHARTEROS_AUTH_ISSUER, "
                    "CHARTEROS_AUTH_AUDIENCE, and CHARTEROS_AUTH_JWKS_URL"
                )
            password = database.password
            if password is not None and password.casefold() in _INSECURE_DATABASE_PASSWORDS:
                raise ValueError("staging/production reject known development database passwords")
            if not self.trusted_ingress_rate_limit_enforced:
                raise ValueError(
                    "staging/production require trusted ingress rate-limit enforcement"
                )
            if self.environment == "production" and self.log_level == "DEBUG":
                raise ValueError("production log level must not be DEBUG")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
