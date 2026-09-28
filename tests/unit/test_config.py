import pytest
from pydantic import ValidationError

from charteros.shared.config import Settings


def test_settings_accept_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_ENVIRONMENT", "test")
    monkeypatch.setenv("CHARTEROS_API_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.api_port == 9000


def test_settings_reject_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTEROS_API_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
