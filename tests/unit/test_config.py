import pytest
from pydantic import ValidationError

from charteros.shared.config import Settings

TEST_DATABASE_URL = "postgresql+psycopg://charteros:test-only@localhost:5432/charteros"


def test_settings_accept_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_ENVIRONMENT", "test")
    monkeypatch.setenv("CHARTEROS_API_PORT", "9000")
    monkeypatch.setenv("CHARTEROS_DATABASE_URL", TEST_DATABASE_URL)

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.api_port == 9000
    assert settings.api_host == "127.0.0.1"


def test_settings_require_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHARTEROS_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("CHARTEROS_API_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_partial_oidc_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("CHARTEROS_AUTH_ISSUER", "https://id.example.test/")

    with pytest.raises(ValidationError, match="OIDC configuration is atomic"):
        Settings(_env_file=None)


def test_production_requires_complete_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_ENVIRONMENT", "production")
    monkeypatch.setenv("CHARTEROS_DATABASE_URL", TEST_DATABASE_URL)

    with pytest.raises(ValidationError, match="staging/production require"):
        Settings(_env_file=None)


def test_production_rejects_known_development_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHARTEROS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "CHARTEROS_DATABASE_URL",
        "postgresql+psycopg://charteros:charteros@db.internal:5432/charteros",
    )
    monkeypatch.setenv("CHARTEROS_AUTH_ISSUER", "https://id.example.test/")
    monkeypatch.setenv("CHARTEROS_AUTH_AUDIENCE", "charteros-api")
    monkeypatch.setenv("CHARTEROS_AUTH_JWKS_URL", "https://id.example.test/.well-known/jwks.json")

    with pytest.raises(ValidationError, match="known development database passwords"):
        Settings(_env_file=None)


def test_production_rejects_debug_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "CHARTEROS_DATABASE_URL",
        "postgresql+psycopg://charteros:strong-random-value@db.internal:5432/charteros",
    )
    monkeypatch.setenv("CHARTEROS_AUTH_ISSUER", "https://id.example.test/")
    monkeypatch.setenv("CHARTEROS_AUTH_AUDIENCE", "charteros-api")
    monkeypatch.setenv("CHARTEROS_AUTH_JWKS_URL", "https://id.example.test/.well-known/jwks.json")
    monkeypatch.setenv("CHARTEROS_LOG_LEVEL", "DEBUG")

    with pytest.raises(ValidationError, match="must not be DEBUG"):
        Settings(_env_file=None)
