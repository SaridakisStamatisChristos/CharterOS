from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from charteros.security.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    Permission,
    PrincipalType,
)
from charteros.shared.config import Settings
from tests.integration.test_pr17_tenders import _setup_tender


class _TokenBackend:
    def __init__(self, principals: dict[str, AuthenticatedPrincipal]) -> None:
        self._principals = principals

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        if authorization_header is None:
            raise AuthenticationError("missing bearer token")
        scheme, separator, token = authorization_header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or " " in token:
            raise AuthenticationError("malformed bearer token")
        principal = self._principals.get(token)
        if principal is None:
            raise AuthenticationError("unknown bearer token")
        return principal


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _principal(
    *,
    subject: str,
    principal_type: PrincipalType,
    permissions: set[Permission],
    buyer_ids: set[UUID] | None = None,
    operator_ids: set[UUID] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject=subject,
        principal_type=principal_type,
        permissions=frozenset(permissions),
        buyer_ids=frozenset(buyer_ids or set()),
        operator_ids=frozenset(operator_ids or set()),
        issuer="pr27-test",
        key_id="pr27-test",
    )


def _fx_body() -> dict[str, object]:
    return {
        "source_currency": "EUR",
        "target_currency": "USD",
        "rate": "1.10000000",
        "source_minor_exponent": 2,
        "target_minor_exponent": 2,
        "fx_source": "pr27-auth-test",
        "fx_source_version": f"pr27-{uuid4()}",
        "fx_timestamp": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }


@pytest.mark.integration
def test_fx_write_requires_authentication_then_admin_capability() -> None:
    settings = _settings()
    buyer = _principal(
        subject="buyer-fx",
        principal_type=PrincipalType.BUYER,
        permissions={Permission.FX_RATE_WRITE},
        buyer_ids={uuid4()},
    )
    admin = _principal(
        subject="admin-fx",
        principal_type=PrincipalType.ADMINISTRATOR,
        permissions={Permission.FX_RATE_WRITE},
    )
    backend = _TokenBackend({"buyer": buyer, "admin": admin})

    with TestClient(create_app(settings, auth_backend=backend)) as client:
        missing = client.post(
            "/v1/fx/rates",
            headers={"Idempotency-Key": f"pr27-missing-{uuid4()}"},
            json=_fx_body(),
        )
        malformed = client.post(
            "/v1/fx/rates",
            headers={
                "Authorization": "Bearer unknown",
                "Idempotency-Key": f"pr27-malformed-{uuid4()}",
            },
            json=_fx_body(),
        )
        forbidden = client.post(
            "/v1/fx/rates",
            headers={
                "Authorization": "Bearer buyer",
                "Idempotency-Key": f"pr27-buyer-{uuid4()}",
            },
            json=_fx_body(),
        )
        allowed = client.post(
            "/v1/fx/rates",
            headers={
                "Authorization": "Bearer admin",
                "Idempotency-Key": f"pr27-admin-{uuid4()}",
            },
            json=_fx_body(),
        )

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert malformed.status_code == 401
    assert forbidden.status_code == 403
    assert allowed.status_code == 201


@pytest.mark.integration
def test_verified_principal_cannot_expand_authority_with_selector_headers() -> None:
    settings = _settings()
    buyer_a = uuid4()
    buyer_b = uuid4()
    operator_a = uuid4()
    operator_b = uuid4()
    buyer = _principal(
        subject="buyer-a",
        principal_type=PrincipalType.BUYER,
        permissions={
            Permission.BUYER_MISSION_READ,
            Permission.OPERATOR_FLEET_READ,
            Permission.CATALOG_WRITE,
            Permission.TENDER_ADMIN_CORRECT,
            Permission.AUDIT_EVIDENCE_READ,
        },
        buyer_ids={buyer_a},
        operator_ids={operator_a},
    )
    operator = _principal(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        permissions={
            Permission.OPERATOR_FLEET_READ,
            Permission.FX_RATE_WRITE,
        },
        operator_ids={operator_a},
    )
    service = _principal(
        subject="service",
        principal_type=PrincipalType.SERVICE,
        permissions=set(Permission),
    )
    backend = _TokenBackend({"buyer": buyer, "operator": operator, "service": service})

    with TestClient(create_app(settings, auth_backend=backend)) as client:
        buyer_spoof = client.get(
            f"/v1/buyer-portal/missions/{uuid4()}",
            headers={"Authorization": "Bearer buyer", "X-Buyer-Id": str(buyer_b)},
        )
        buyer_to_operator = client.get(
            "/v1/operator-portal/fleet",
            headers={
                "Authorization": "Bearer buyer",
                "X-Operator-Id": str(operator_a),
            },
        )
        operator_spoof = client.get(
            "/v1/operator-portal/fleet",
            headers={
                "Authorization": "Bearer operator",
                "X-Operator-Id": str(operator_b),
            },
        )
        operator_to_admin = client.post(
            "/v1/fx/rates",
            headers={
                "Authorization": "Bearer operator",
                "Idempotency-Key": f"pr27-operator-admin-{uuid4()}",
            },
            json=_fx_body(),
        )
        buyer_to_catalog_admin = client.post(
            "/v1/organizations",
            headers={
                "Authorization": "Bearer buyer",
                "Idempotency-Key": f"pr27-buyer-catalog-{uuid4()}",
            },
            json={"type": "buyer", "legal_name": "Forbidden", "country": "GR"},
        )
        buyer_to_tender_admin = client.post(
            f"/v1/tenders/{uuid4()}/admin-corrections",
            headers={
                "Authorization": "Bearer buyer",
                "Idempotency-Key": f"pr27-buyer-tender-admin-{uuid4()}",
            },
            json={
                "actor_id": str(uuid4()),
                "target_type": "quote",
                "target_id": str(uuid4()),
                "field_name": "status",
                "original_value": "old",
                "replacement_value": "new",
                "reason": "authorization regression",
                "causation_event_id": str(uuid4()),
            },
        )
        service_to_human = client.get(
            "/v1/operator-portal/fleet",
            headers={
                "Authorization": "Bearer service",
                "X-Operator-Id": str(operator_a),
            },
        )
        unauthorized_evidence = client.get(
            f"/v1/evidence/missions/{uuid4()}",
            headers={"Authorization": "Bearer buyer", "X-Buyer-Id": str(buyer_a)},
        )

    assert buyer_spoof.status_code == 403
    assert buyer_to_operator.status_code == 403
    assert operator_spoof.status_code == 403
    assert operator_to_admin.status_code == 403
    assert buyer_to_catalog_admin.status_code == 403
    assert buyer_to_tender_admin.status_code == 403
    assert service_to_human.status_code == 403
    # This buyer has the evidence capability above, so authorization passes and the missing
    # resource is handled by the domain boundary without leaking a different tenant's data.
    assert unauthorized_evidence.status_code == 404


@pytest.mark.integration
def test_authenticated_principal_without_evidence_capability_is_forbidden() -> None:
    settings = _settings()
    buyer_id = uuid4()
    buyer = _principal(
        subject="buyer-no-evidence",
        principal_type=PrincipalType.BUYER,
        permissions={Permission.BUYER_MISSION_READ},
        buyer_ids={buyer_id},
    )
    backend = _TokenBackend({"buyer": buyer})

    with TestClient(create_app(settings, auth_backend=backend)) as client:
        response = client.get(
            f"/v1/evidence/missions/{uuid4()}",
            headers={"Authorization": "Bearer buyer", "X-Buyer-Id": str(buyer_id)},
        )

    assert response.status_code == 403


@pytest.mark.integration
def test_sealed_tender_operator_cannot_use_competitor_invitation() -> None:
    settings = _settings()
    # Existing test fixtures authenticate through the injected pytest-only trusted admin seam.
    with TestClient(create_app(settings)) as setup_client:
        _mission_id, tender_id, _departure, suppliers = _setup_tender(
            setup_client,
            suffix="27",
        )

    operator_id = UUID(suppliers[0]["operator_id"])
    operator = _principal(
        subject="sealed-operator-a",
        principal_type=PrincipalType.OPERATOR,
        permissions={Permission.TENDER_SUPPLIER_READ},
        operator_ids={operator_id},
    )
    backend = _TokenBackend({"operator": operator})
    own_invitation = suppliers[0]["invitation_id"]
    competitor_invitation = suppliers[1]["invitation_id"]

    with TestClient(create_app(settings, auth_backend=backend)) as client:
        competitor = client.get(
            f"/v1/tenders/{tender_id}/supplier-view",
            headers={
                "Authorization": "Bearer operator",
                "X-Operator-Id": str(operator_id),
                "X-Tender-Invitation-Id": competitor_invitation,
            },
        )
        own = client.get(
            f"/v1/tenders/{tender_id}/supplier-view",
            headers={
                "Authorization": "Bearer operator",
                "X-Operator-Id": str(operator_id),
                "X-Tender-Invitation-Id": own_invitation,
            },
        )

    assert competitor.status_code == 403
    assert own.status_code == 200
    own_quote_ids = {str(item["id"]) for item in own.json()["own_quotes"]}
    assert suppliers[0]["quote_id"] in own_quote_ids
    assert suppliers[1]["quote_id"] not in own_quote_ids
