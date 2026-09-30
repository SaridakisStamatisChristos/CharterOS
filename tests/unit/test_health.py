from contextlib import AbstractContextManager
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from apps.api.main import create_app
from charteros.shared.config import Settings

TEST_DATABASE_URL = "postgresql+psycopg://charteros:test-only@localhost:5432/charteros"


class _UnavailableEngine:
    def connect(self) -> AbstractContextManager[Any]:
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    def dispose(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        environment="test",
        service_name="charteros-test",
        database_url=TEST_DATABASE_URL,
        _env_file=None,
    )


def test_health_endpoint_is_stable_and_correlated() -> None:
    settings = _settings()

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/health",
            headers={"X-Correlation-ID": "7eb2f9b8-fad3-4c62-b0cb-b33f582ac51a"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "charteros-test",
        "version": "0.1.0",
        "environment": "test",
    }
    assert response.headers["X-Correlation-ID"] == "7eb2f9b8-fad3-4c62-b0cb-b33f582ac51a"


def test_invalid_correlation_id_is_replaced() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/health", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] != "not-a-uuid"


def test_readiness_fails_closed_when_database_is_unavailable() -> None:
    app = create_app(_settings())
    app.state.engine.dispose()
    app.state.engine = _UnavailableEngine()

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
