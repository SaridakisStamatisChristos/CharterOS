from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.api.main import create_app
from apps.resource_cleanup.main import cleanup_resource_state
from charteros.application.resource_limits import AbuseBudget
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import IdempotencyRecordRow
from charteros.infrastructure.db.repositories.abuse import SqlAlchemyRateBudgetRepository
from charteros.security.auth import (
    AuthenticatedPrincipal,
    Permission,
    PrincipalType,
)
from charteros.shared.config import Settings


def _settings(**overrides: object) -> Settings:
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


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr44_postgresql_rate_budget_is_atomic_across_independent_pools() -> None:
    settings = _settings()
    first_engine = build_engine(settings)
    second_engine = build_engine(settings)
    first_factory = build_session_factory(first_engine)
    second_factory = build_session_factory(second_engine)
    barrier = Barrier(12)
    identity_digest = hashlib.sha256(str(uuid4()).encode()).hexdigest()

    def consume(index: int) -> bool:
        barrier.wait(timeout=10)
        factory = first_factory if index % 2 == 0 else second_factory
        with factory.begin() as session:
            return SqlAlchemyRateBudgetRepository(session).consume(
                budget=AbuseBudget.MATCHING,
                identity_digest=identity_digest,
                limit=5,
                window_seconds=60,
            ).allowed

    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            decisions = list(executor.map(consume, range(12)))

        assert sum(decisions) == 5
    finally:
        first_engine.dispose()
        second_engine.dispose()


@pytest.mark.integration
def test_pr44_expensive_budget_is_shared_across_api_replicas() -> None:
    settings = _settings(api_matching_requests_per_window=2)
    mission_id = uuid4()

    with (
        TestClient(create_app(settings)) as first,
        TestClient(create_app(settings)) as second,
    ):
        one = first.get(f"/v1/missions/{mission_id}/matches")
        two = second.get(f"/v1/missions/{mission_id}/matches")
        blocked = first.get(f"/v1/missions/{mission_id}/matches")

    assert one.status_code == 404
    assert two.status_code == 404
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "resource_limit_exceeded"}
    assert int(blocked.headers["Retry-After"]) >= 1


@pytest.mark.integration
def test_pr44_budget_identity_is_tenant_aware() -> None:
    buyer_a = uuid4()
    buyer_b = uuid4()
    principal = AuthenticatedPrincipal(
        subject=f"pr44-buyer-{uuid4()}",
        principal_type=PrincipalType.BUYER,
        permissions=frozenset({Permission.BUYER_MISSION_READ}),
        buyer_ids=frozenset({buyer_a, buyer_b}),
        operator_ids=frozenset(),
        issuer="pytest-pr44",
        key_id="pytest-pr44",
    )
    settings = _settings(api_matching_requests_per_window=1)
    app = create_app(
        settings,
        auth_backend=_StaticAuthenticationBackend(principal),
    )
    mission_id = uuid4()

    with TestClient(app) as client:
        first_a = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/suppliers",
            headers={"X-Buyer-Id": str(buyer_a)},
        )
        blocked_a = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/suppliers",
            headers={"X-Buyer-Id": str(buyer_a)},
        )
        first_b = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/suppliers",
            headers={"X-Buyer-Id": str(buyer_b)},
        )

    assert first_a.status_code == 404
    assert blocked_a.status_code == 429
    assert first_b.status_code == 404


@pytest.mark.integration
def test_pr44_idempotency_cleanup_is_age_and_batch_bounded() -> None:
    now = datetime.now(UTC)
    settings = _settings(
        idempotency_retention_days=30,
        idempotency_cleanup_batch_size=2,
    )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    prefix = f"pr44-cleanup-{uuid4()}"
    try:
        with factory.begin() as session:
            for index, age_days in enumerate((45, 44, 43, 1)):
                session.add(
                    IdempotencyRecordRow(
                        scope=f"{prefix}:{index}",
                        key="cleanup",
                        request_hash=f"{index:064x}",
                        status_code=200,
                        response_body={"index": index},
                        created_at=now - timedelta(days=age_days),
                    )
                )

        result = cleanup_resource_state(settings, now=now)
        assert result.idempotency_deleted == 2

        with factory() as session:
            remaining = session.scalar(
                select(func.count())
                .select_from(IdempotencyRecordRow)
                .where(IdempotencyRecordRow.scope.like(f"{prefix}%"))
            )
            old_remaining = session.scalar(
                select(func.count())
                .select_from(IdempotencyRecordRow)
                .where(
                    IdempotencyRecordRow.scope.like(f"{prefix}%"),
                    IdempotencyRecordRow.created_at < now - timedelta(days=30),
                )
            )
        assert remaining == 2
        assert old_remaining == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr44_idempotency_response_storage_has_database_byte_ceiling() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(
                    IdempotencyRecordRow(
                        scope=f"pr44-response-bound:{uuid4()}",
                        key="oversized",
                        request_hash="a" * 64,
                        status_code=200,
                        response_body={"payload": "x" * 270_000},
                    )
                )
    finally:
        engine.dispose()
