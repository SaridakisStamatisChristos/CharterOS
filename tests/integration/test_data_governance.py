from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.main import create_app
from charteros.application.data_governance import (
    DataGovernanceService,
    LifecycleOperation,
    TenantKind,
)
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.evidence_integrity import assert_evidence_integrity
from charteros.infrastructure.db.models.catalog import OrganizationRow, OutboxEventRow
from charteros.infrastructure.db.models.governance import (
    DataGovernanceLegalHoldRow,
    DataGovernanceLifecycleOperationRow,
)
from charteros.infrastructure.db.repositories.governance import (
    SqlAlchemyDataGovernanceRepository,
)
from charteros.security.auth import (
    AuthenticatedPrincipal,
    Permission,
    PrincipalType,
)
from charteros.shared.config import Settings


def _settings(**overrides: Any) -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(
        environment="test",
        database_url=database_url,
        _env_file=None,
        **overrides,
    )


class _StaticAuthenticationBackend:
    def __init__(self, principal: AuthenticatedPrincipal) -> None:
        self._principal = principal

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        del authorization_header
        return self._principal


def _principal(
    *,
    principal_type: PrincipalType,
    permissions: frozenset[Permission],
    buyer_ids: frozenset[UUID] = frozenset(),
    operator_ids: frozenset[UUID] = frozenset(),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject=f"pr45-{principal_type.value}-{uuid4()}",
        principal_type=principal_type,
        permissions=permissions,
        buyer_ids=buyer_ids,
        operator_ids=operator_ids,
        issuer="pytest-pr45",
        key_id="pytest-pr45",
    )


def _insert_buyer(settings: Settings) -> UUID:
    tenant_id = uuid4()
    legal_name = f"PR45 Buyer {tenant_id}"
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory.begin() as session:
            session.add(
                OrganizationRow(
                    id=tenant_id,
                    version=1,
                    type="buyer",
                    legal_name=legal_name,
                    legal_name_key=legal_name.casefold(),
                    trading_name=None,
                    country="GR",
                    status="active",
                )
            )
    finally:
        engine.dispose()
    return tenant_id


@pytest.mark.integration
def test_pr45_closure_is_idempotent_and_evidence_blocks_erasure() -> None:
    settings = _settings()
    tenant_id = _insert_buyer(settings)
    admin = _principal(
        principal_type=PrincipalType.ADMINISTRATOR,
        permissions=frozenset({Permission.TENANT_GOVERNANCE_WRITE}),
    )

    with TestClient(
        create_app(settings, auth_backend=_StaticAuthenticationBackend(admin))
    ) as client:
        first = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/close",
            headers={"Idempotency-Key": f"pr45-close-{tenant_id}"},
        )
        replay = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/close",
            headers={"Idempotency-Key": f"pr45-close-{tenant_id}"},
        )
        blocked = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/erase",
            headers={"Idempotency-Key": f"pr45-erase-{tenant_id}"},
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert first.json()["status"] == "completed"
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["report"]["blockers"]["outbox_events"] >= 1

    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            organization = session.get(OrganizationRow, tenant_id)
            assert organization is not None
            assert organization.status == "inactive"
            closure_count = session.scalar(
                select(func.count())
                .select_from(DataGovernanceLifecycleOperationRow)
                .where(
                    DataGovernanceLifecycleOperationRow.tenant_kind == "buyer",
                    DataGovernanceLifecycleOperationRow.tenant_id == tenant_id,
                    DataGovernanceLifecycleOperationRow.operation
                    == LifecycleOperation.CLOSURE.value,
                )
            )
            outbox_count = session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.aggregate_type == "organization",
                    OutboxEventRow.aggregate_id == tenant_id,
                )
            )
            assert closure_count == 1
            assert outbox_count == 1
            assert_evidence_integrity(session)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr45_legal_hold_blocks_erasure_until_explicit_release() -> None:
    settings = _settings()
    tenant_id = _insert_buyer(settings)
    admin = _principal(
        principal_type=PrincipalType.ADMINISTRATOR,
        permissions=frozenset(
            {
                Permission.TENANT_GOVERNANCE_READ,
                Permission.TENANT_GOVERNANCE_WRITE,
            }
        ),
    )

    with TestClient(
        create_app(settings, auth_backend=_StaticAuthenticationBackend(admin))
    ) as client:
        hold = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/legal-holds",
            json={"reason": "Preserve records for active contractual dispute"},
        )
        assert hold.status_code == 200
        hold_id = UUID(hold.json()["id"])

        blocked = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/erase",
            headers={"Idempotency-Key": f"pr45-held-erase-{tenant_id}"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        assert blocked.json()["report"]["blockers"]["active_legal_hold"] == 1

        released = client.post(
            f"/v1/governance/legal-holds/{hold_id}/release",
            json={"reason": "Contractual dispute resolved"},
        )
        assert released.status_code == 200
        assert released.json()["status"] == "released"

        erased = client.post(
            f"/v1/governance/tenants/buyer/{tenant_id}/erase",
            headers={"Idempotency-Key": f"pr45-released-erase-{tenant_id}"},
        )
        assert erased.status_code == 200
        assert erased.json()["status"] == "completed"
        assert erased.json()["report"]["immutable_evidence_preserved"] is True

    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            assert session.get(OrganizationRow, tenant_id) is None
            persisted_hold = session.get(DataGovernanceLegalHoldRow, hold_id)
            assert persisted_hold is not None
            assert persisted_hold.status == "released"
            assert_evidence_integrity(session)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr45_tenant_export_is_scoped_and_cross_tenant_access_is_denied() -> None:
    settings = _settings()
    tenant_a = _insert_buyer(settings)
    tenant_b = _insert_buyer(settings)
    buyer = _principal(
        principal_type=PrincipalType.BUYER,
        permissions=frozenset({Permission.TENANT_DATA_EXPORT}),
        buyer_ids=frozenset({tenant_a}),
    )

    with TestClient(
        create_app(settings, auth_backend=_StaticAuthenticationBackend(buyer))
    ) as client:
        own = client.get(f"/v1/governance/tenants/buyer/{tenant_a}/export")
        denied = client.get(f"/v1/governance/tenants/buyer/{tenant_b}/export")

    assert own.status_code == 200
    document = own.json()
    assert document["tenant_id"] == str(tenant_a)
    assert document["master_data"]["organization"]["id"] == str(tenant_a)
    assert document["mission_evidence"] == []
    assert "authentication_security_logs" in document["excluded_surfaces"]
    assert "idempotency_records" in document["excluded_surfaces"]
    assert str(tenant_b) not in json.dumps(document, sort_keys=True)

    assert denied.status_code == 403
    assert denied.json() == {"detail": "forbidden"}


@pytest.mark.integration
def test_pr45_erasure_rolls_back_if_audit_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    tenant_id = _insert_buyer(settings)
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    try:
        with (
            pytest.raises(RuntimeError, match="synthetic governance audit failure"),
            factory.begin() as session,
        ):
            repository = SqlAlchemyDataGovernanceRepository(session)

            def fail_audit(**_kwargs: object) -> None:
                raise RuntimeError("synthetic governance audit failure")

            monkeypatch.setattr(repository, "append_governance_event", fail_audit)
            DataGovernanceService(repository).erase_tenant(
                tenant_kind=TenantKind.BUYER,
                tenant_id=tenant_id,
                request_key_digest="1" * 64,
                request_hash="2" * 64,
                actor_subject_digest="3" * 64,
                recorded_at=datetime.now(UTC),
            )

        with factory() as session:
            assert session.get(OrganizationRow, tenant_id) is not None
            operation_count = session.scalar(
                select(func.count())
                .select_from(DataGovernanceLifecycleOperationRow)
                .where(
                    DataGovernanceLifecycleOperationRow.tenant_kind == "buyer",
                    DataGovernanceLifecycleOperationRow.tenant_id == tenant_id,
                )
            )
            assert operation_count == 0
            assert_evidence_integrity(session)
    finally:
        engine.dispose()
