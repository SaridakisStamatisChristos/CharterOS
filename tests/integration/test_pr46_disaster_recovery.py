from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from charteros.application.outbox import OutboxDeliveryStatus, OutboxEnvelope
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import IdempotencyRecordRow, OutboxEventRow
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.infrastructure.db.repositories.outbox import (
    SqlAlchemyIdempotentConsumerRunner,
    SqlAlchemyOutboxDeliveryRepository,
)
from charteros.infrastructure.db.repositories.recovery import (
    SqlAlchemyRecoveryVerificationRepository,
)
from charteros.shared.config import Settings


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _event(
    *,
    event_id: UUID,
    aggregate_id: UUID,
    recorded_at: datetime,
    aggregate_type: str = "pr46_recovery",
) -> OutboxEnvelope:
    canonical_json = json.dumps(
        {
            "event_id": str(event_id),
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "aggregate_version": 1,
            "event_type": "PR46_RECOVERY_EVENT",
            "event_version": 1,
            "occurred_at": recorded_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "actor_id": None,
            "correlation_id": None,
            "causation_id": None,
            "payload": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return OutboxEnvelope(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=1,
        event_type="PR46_RECOVERY_EVENT",
        event_version=1,
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=canonical_json,
    )


def _persist_event(
    session: Session,
    envelope: OutboxEnvelope,
    *,
    status: OutboxDeliveryStatus,
    available_at: datetime,
    published_at: datetime | None = None,
    lease_owner: str | None = None,
    lease_token: UUID | None = None,
    lease_expires_at: datetime | None = None,
    publish_attempts: int = 0,
    delivery_attempts: int = 0,
) -> None:
    session.add(
        OutboxEventRow(
            event_id=envelope.event_id,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            aggregate_version=envelope.aggregate_version,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            occurred_at=envelope.occurred_at,
            recorded_at=envelope.recorded_at,
            actor_id=None,
            correlation_id=None,
            causation_id=None,
            canonical_json=envelope.canonical_json,
            delivery_status=status.value,
            available_at=available_at,
            last_attempt_at=None,
            last_error=None,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            published_at=published_at,
            poisoned_at=None,
            publish_attempts=publish_attempts,
            delivery_attempts=delivery_attempts,
        )
    )


@pytest.mark.integration
def test_pr46_restore_verification_is_read_only_and_checks_canonical_invariants() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            snapshot = SqlAlchemyRecoveryVerificationRepository(session).snapshot(
                verification_time=datetime.now(UTC)
            )
            assert snapshot.schema_revision == "0025_commercial_data_governance"
            assert snapshot.evidence_violation_count == 0
            assert snapshot.capacity_overlap_count == 0
            assert snapshot.orphan_references == ()

            with pytest.raises(DBAPIError):
                session.execute(
                    text(
                        "INSERT INTO organizations "
                        "(id, version, type, legal_name, legal_name_key, country, "
                        "status) VALUES (:id, 1, 'buyer', 'must-not-write', "
                        "'must-not-write', 'GR', 'active')"
                    ),
                    {"id": uuid4()},
                )
            session.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.regression
def test_pr46_graph_can_be_rebuilt_and_verified_from_canonical_outbox_history() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 46_000_000 + (uuid4().int % 1_000_000)
    try:
        report = SqlAlchemyGraphProjectionStore(factory).rebuild(
            version,
            now=datetime.now(UTC),
        )
        assert report.ok, report.issues
        assert report.reference_digest == report.persisted_digest
        assert report.event_count == report.receipt_count
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr46_expired_outbox_lease_is_safely_reclaimable_after_restore() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    envelope = _event(
        event_id=uuid4(),
        aggregate_id=uuid4(),
        recorded_at=datetime(1990, 1, 1, tzinfo=UTC),
    )
    old_lease = uuid4()
    try:
        with factory.begin() as session:
            _persist_event(
                session,
                envelope,
                status=OutboxDeliveryStatus.IN_FLIGHT,
                available_at=now - timedelta(minutes=10),
                lease_owner="pre-restore-worker",
                lease_token=old_lease,
                lease_expires_at=now - timedelta(minutes=1),
                publish_attempts=1,
                delivery_attempts=1,
            )

        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        claims = repository.claim_batch(
            worker_id="pr46-recovery-worker",
            now=now,
            batch_size=10_000,
            lease_seconds=30,
            max_attempts=8,
        )
        recovered = next(claim for claim in claims if claim.envelope.event_id == envelope.event_id)
        assert recovered.lease_token != old_lease
        assert recovered.attempt == 2
        repository.mark_delivered(
            event_id=envelope.event_id,
            lease_token=recovered.lease_token,
            delivered_at=now + timedelta(seconds=1),
        )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr46_redelivery_cannot_repeat_transactional_consumer_effect() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    envelope = _event(event_id=uuid4(), aggregate_id=uuid4(), recorded_at=now)
    scope = f"pr46:consumer-effect:{uuid4()}"
    effect_key = str(uuid4())

    def handler(session: Session, _envelope: OutboxEnvelope) -> None:
        session.add(
            IdempotencyRecordRow(
                scope=scope,
                key=effect_key,
                request_hash="4" * 64,
                status_code=201,
                response_body={"effect": "once"},
                created_at=now,
            )
        )

    try:
        with factory.begin() as session:
            _persist_event(
                session,
                envelope,
                status=OutboxDeliveryStatus.DELIVERED,
                available_at=now,
                published_at=now,
            )

        runner = SqlAlchemyIdempotentConsumerRunner(factory)
        assert runner.consume(
            consumer_name="pr46-recovery-consumer",
            consumer_version=1,
            envelope=envelope,
            processed_at=now,
            handler=handler,
        )

        # Simulate restored delivery metadata that makes the same event eligible for redelivery.
        with factory.begin() as session:
            event = session.get(OutboxEventRow, envelope.event_id)
            assert event is not None
            event.delivery_status = OutboxDeliveryStatus.RETRY.value
            event.published_at = None
            event.available_at = now
            event.last_error = None
            event.lease_owner = None
            event.lease_token = None
            event.lease_expires_at = None

        assert not runner.consume(
            consumer_name="pr46-recovery-consumer",
            consumer_version=1,
            envelope=envelope,
            processed_at=now + timedelta(seconds=1),
            handler=handler,
        )

        with factory() as session:
            effect_count = session.scalar(
                select(func.count())
                .select_from(IdempotencyRecordRow)
                .where(
                    IdempotencyRecordRow.scope == scope,
                    IdempotencyRecordRow.key == effect_key,
                )
            )
            receipt_count = session.scalar(
                select(func.count())
                .select_from(OutboxConsumerReceiptRow)
                .where(
                    OutboxConsumerReceiptRow.consumer_name == "pr46-recovery-consumer",
                    OutboxConsumerReceiptRow.event_id == envelope.event_id,
                )
            )
        assert effect_count == 1
        assert receipt_count == 1
    finally:
        engine.dispose()
