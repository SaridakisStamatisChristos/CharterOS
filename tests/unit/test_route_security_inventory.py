from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.route_security import ROUTE_POLICIES, route_policy
from charteros.application.resource_limits import AbuseBudget
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


def test_expensive_routes_have_explicit_resource_budgets() -> None:
    matching_routes = {
        ("GET", "/v1/missions/{mission_id}/matches"),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/suppliers"),
    }
    evidence_routes = {
        ("GET", "/v1/buyer-portal/missions/{mission_id}/audit"),
        ("GET", "/v1/evidence/missions/{mission_id}"),
        ("GET", "/v1/evidence/bookings/{booking_id}"),
        ("GET", "/v1/evidence/disruptions/{disruption_id}"),
        ("GET", "/v1/evidence/reconciliations/{reconciliation_id}"),
    }
    repositioning_routes = {
        ("GET", "/v1/optimization/repositioning"),
        ("GET", "/v1/operator-portal/empty-legs"),
    }

    for route in matching_routes:
        policy = route_policy(*route)
        assert policy is not None
        assert policy.abuse_budget is AbuseBudget.MATCHING

    for route in evidence_routes:
        policy = route_policy(*route)
        assert policy is not None
        assert policy.abuse_budget is AbuseBudget.EVIDENCE

    for route in repositioning_routes:
        policy = route_policy(*route)
        assert policy is not None
        assert policy.abuse_budget is AbuseBudget.REPOSITIONING

    for route, policy in ROUTE_POLICIES.items():
        if route[1].startswith("/v1/graph/"):
            assert policy.abuse_budget is AbuseBudget.GRAPH

    ordinary = route_policy("POST", "/v1/organizations")
    assert ordinary is not None
    assert ordinary.abuse_budget is None
