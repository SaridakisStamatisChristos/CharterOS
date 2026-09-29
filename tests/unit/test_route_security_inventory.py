from __future__ import annotations

from typing import cast

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.route_security import ROUTE_POLICIES
from apps.api.security import authorize_request
from charteros.security.auth import RejectingAuthenticationBackend
from charteros.shared.config import Settings


def _settings() -> Settings:
    return Settings(environment="test", _env_file=None)


def test_every_v1_route_has_exactly_one_security_policy_and_dependency() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())
    routes: list[APIRoute] = []
    for raw_route in app.routes:
        path = getattr(raw_route, "path", None)
        if isinstance(path, str) and path.startswith("/v1"):
            routes.append(cast(APIRoute, raw_route))
    actual: set[tuple[str, str]] = set()
    for route in routes:
        assert route.methods is not None
        actual.update((method, route.path) for method in route.methods)

    assert actual == set(ROUTE_POLICIES)
    for route in routes:
        assert any(
            dependency.call is authorize_request for dependency in route.dependant.dependencies
        )


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
