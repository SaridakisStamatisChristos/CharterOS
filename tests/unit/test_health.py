from fastapi.testclient import TestClient

from apps.api.main import create_app
from charteros.shared.config import Settings


def test_health_endpoint_is_stable_and_correlated() -> None:
    settings = Settings(environment="test", service_name="charteros-test", _env_file=None)

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
    settings = Settings(environment="test", _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] != "not-a-uuid"
