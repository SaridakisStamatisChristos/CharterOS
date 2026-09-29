from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.route_security import ROUTE_POLICIES
from charteros.security.auth import RejectingAuthenticationBackend
from charteros.shared.config import Settings

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})


def _settings() -> Settings:
    return Settings(environment="test", _env_file=None)


def test_every_externally_exposed_v1_operation_has_exactly_one_security_policy() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())
    schema = app.openapi()
    raw_paths = schema.get("paths")
    assert isinstance(raw_paths, dict)

    actual: set[tuple[str, str]] = set()
    for raw_path, raw_operations in raw_paths.items():
        assert isinstance(raw_path, str)
        if not raw_path.startswith("/v1"):
            continue
        assert isinstance(raw_operations, dict)
        operations = cast(dict[str, object], raw_operations)
        for method in operations:
            normalized_method = method.upper()
            if normalized_method in _HTTP_METHODS:
                actual.add((normalized_method, raw_path))

    assert actual == set(ROUTE_POLICIES)


def test_health_is_public_but_v1_fails_closed_without_auth_configuration() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())
    with TestClient(app) as client:
        health = client.get("/health")
        denied = client.post(
            "/v1/fx/rates",
            headers={"Idempotency-Key": "route-inventory-fx"},
            json={},
        )

    assert health.status_code == 200
    assert denied.status_code == 401
    assert denied.headers["WWW-Authenticate"] == "Bearer"
