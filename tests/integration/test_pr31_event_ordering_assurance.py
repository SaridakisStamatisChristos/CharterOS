from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.graph_projection import (
    PROJECTION_NAME,
    GraphProjectionGapError,
    consumer_name,
)
from charteros.application.outbox import OutboxDeliveryStatus, OutboxEnvelope
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphAggregateCursorRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
)
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.infrastructure.db.repositories.outbox import SqlAlchemyOutboxDeliveryRepository
from charteros.outbox import OutboxWorker, OutboxWorkerConfig
from charteros.shared.config import Settings

BASE = datetime(1990, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2190, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _document(
    *,
    event_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    recorded_at: datetime,
    payload: dict[str, object],
) -> str:
    timestamp = recorded_at.isoformat().replace("+00:00", "Z")
    return json.dumps(
        {
            "event_id": str(event_id),
            "aggregate_type": "airport",
            "aggregate_id": str(aggregate_id),
            "aggregate_version": aggregate_version,
            "event_type": "AIRPORT_REGISTERED",
            "event_version": 1,
            "occurred_at": timestamp,
            "recorded_at": timestamp,
            "actor_id": None,
            "correlation_id": None,
            "causation_id": None,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _seed_airport_event(
    factory: sessionmaker[Session],
    *,
    aggregate_id: UUID,
    aggregate_version: int,
    ordinal: int,
    available_at: datetime = FAR_FUTURE,
) -> OutboxEnvelope:
    event_id = uuid4()
    recorded_at = BASE + timedelta(seconds=ordinal)
    payload: dict[str, object] = {
        "icao": f"P{ordinal % 1000:03d}",
        "iata": None,
        "timezone": "UTC",
        "sequence": aggregate_version,
    }
    canonical_json = _document(
        event_id=event_id,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        recorded_at=recorded_at,
        payload=payload,
    )
    with factory.begin() as session:
        session.add(
            OutboxEventRow(
                event_id=event_id,
                aggregate_type="airport",
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                event_type="AIRPORT_REGISTERED",
                event_version=1,
                occurred_at=recorded_at,
                recorded_at=recorded_at,
                actor_id=None,
                correlation_id=None,
                causation_id=None,
                canonical_json=canonical_json,
                delivery_status=OutboxDeliveryStatus.PENDING.value,
                available_at=available_at,
                last_attempt_at=None,
                last_error=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                published_at=None,
                poisoned_at=None,
                publish_attempts=0,
                delivery_attempts=0,
            )
        )
    return OutboxEnvelope(
        event_id=event_id,
        aggregate_type="airport",
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type="AIRPORT_REGISTERED",
        event_version=1,
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=canonical_json,
    )


class _ProjectionPublisher:
    def __init__(self, store: SqlAlchemyGraphProjectionStore, version: int) -> None:
        self._store = store
        self._version = version

    def publish(self, envelope: OutboxEnvelope) -> None:
        self._store.consume_into_version(
            self._version,
            envelope,
            processed_at=envelope.recorded_at,
        )


def _worker(
    factory: sessionmaker[Session],
    store: SqlAlchemyGraphProjectionStore,
    version: int,
    *,
    worker_id: str,
    max_attempts: int = 3,
) -> OutboxWorker:
    return OutboxWorker(
        repository=SqlAlchemyOutboxDeliveryRepository(factory),
        publisher=_ProjectionPublisher(store, version),
        worker_id=worker_id,
        config=OutboxWorkerConfig(
            batch_size=1,
            lease_seconds=1,
            max_attempts=max_attempts,
            backoff_base_seconds=1,
            backoff_max_seconds=4,
            poll_interval_seconds=0.01,
        ),
    )


def _checkpoint(factory: sessionmaker[Session], version: int) -> GraphProjectionCheckpointRow:
    with factory() as session:
        checkpoint = session.get(
            GraphProjectionCheckpointRow,
            (PROJECTION_NAME, version),
        )
        assert checkpoint is not None
        session.expunge(checkpoint)
        return checkpoint


@pytest.mark.integration
@pytest.mark.regression
def test_same_aggregate_reversed_gap_then_converges_and_delayed_old_event_is_idempotent() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3101
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        aggregate_id = uuid4()

        # recorded_at deliberately disagrees with causal aggregate_version order.
        second = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=2,
            ordinal=1,
        )
        first = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=1,
            ordinal=2,
        )

        with pytest.raises(GraphProjectionGapError, match="expected version 1, got 2"):
            store.consume_into_version(version, second, processed_at=second.recorded_at)

        assert store.consume_into_version(version, first, processed_at=first.recorded_at)
        assert store.consume_into_version(version, second, processed_at=second.recorded_at)
        assert not store.consume_into_version(version, first, processed_at=first.recorded_at)

        with factory() as session:
            cursor = session.get(
                GraphAggregateCursorRow,
                (PROJECTION_NAME, version, "airport", aggregate_id),
            )
            node = session.get(
                GraphNodeRow,
                (PROJECTION_NAME, version, "airport", aggregate_id),
            )
            assert cursor is not None
            assert cursor.last_aggregate_version == 2
            assert node is not None
            assert node.attributes["sequence"] == 2
        assert _checkpoint(factory, version).processed_event_count == 2
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_two_workers_racing_adjacent_versions_converge_without_silent_divergence() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3102
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        aggregate_id = uuid4()
        first = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=1,
            ordinal=10,
        )
        second = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=2,
            ordinal=9,
        )
        barrier = Barrier(2)

        def consume(envelope: OutboxEnvelope) -> str:
            barrier.wait()
            try:
                applied = SqlAlchemyGraphProjectionStore(factory).consume_into_version(
                    version,
                    envelope,
                    processed_at=envelope.recorded_at,
                )
            except GraphProjectionGapError:
                return "gap"
            return "applied" if applied else "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(consume, first)
            b = executor.submit(consume, second)
            outcomes = {a.result(), b.result()}

        assert outcomes <= {"applied", "gap"}
        assert "applied" in outcomes

        # If v2 won the lock first it must have failed closed as a gap; retry converges.
        store.consume_into_version(version, second, processed_at=second.recorded_at)

        with factory() as session:
            cursor = session.get(
                GraphAggregateCursorRow,
                (PROJECTION_NAME, version, "airport", aggregate_id),
            )
            assert cursor is not None
            assert cursor.last_aggregate_version == 2
            assert (
                session.get(OutboxConsumerReceiptRow, (consumer_name(version), first.event_id))
                is not None
            )
            assert (
                session.get(OutboxConsumerReceiptRow, (consumer_name(version), second.event_id))
                is not None
            )
        assert _checkpoint(factory, version).processed_event_count == 2
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.regression
def test_unrelated_aggregates_may_interleave_without_global_timestamp_order() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3103
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        first_id = uuid4()
        second_id = uuid4()
        a1 = _seed_airport_event(
            factory, aggregate_id=first_id, aggregate_version=1, ordinal=30
        )
        a2 = _seed_airport_event(
            factory, aggregate_id=first_id, aggregate_version=2, ordinal=27
        )
        b1 = _seed_airport_event(
            factory, aggregate_id=second_id, aggregate_version=1, ordinal=29
        )
        b2 = _seed_airport_event(
            factory, aggregate_id=second_id, aggregate_version=2, ordinal=28
        )

        for envelope in (a1, b1, b2, a2):
            assert store.consume_into_version(
                version,
                envelope,
                processed_at=envelope.recorded_at,
            )

        with factory() as session:
            for aggregate_id in (first_id, second_id):
                cursor = session.get(
                    GraphAggregateCursorRow,
                    (PROJECTION_NAME, version, "airport", aggregate_id),
                )
                assert cursor is not None
                assert cursor.last_aggregate_version == 2
        assert _checkpoint(factory, version).processed_event_count == 4
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_worker_crash_after_claim_reclaims_expired_lease_and_projects_once() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3104
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        aggregate_id = uuid4()
        event = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=1,
            ordinal=40,
            available_at=BASE,
        )
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        claimed_at = BASE + timedelta(minutes=1)
        first_claim = repository.claim_batch(
            worker_id="pr31-crashed-before-publish",
            now=claimed_at,
            batch_size=1,
            lease_seconds=1,
            max_attempts=3,
        )
        assert len(first_claim) == 1
        assert first_claim[0].envelope.event_id == event.event_id

        # Simulate process death: no publish and no acknowledgement.
        recovered = _worker(
            factory,
            store,
            version,
            worker_id="pr31-recovery-after-claim",
        ).run_once(now=claimed_at + timedelta(seconds=2))
        assert recovered.delivered == 1

        with factory() as session:
            row = session.get(OutboxEventRow, event.event_id)
            assert row is not None
            assert row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert row.publish_attempts == 2
            assert row.delivery_attempts == 2
            assert (
                session.get(OutboxConsumerReceiptRow, (consumer_name(version), event.event_id))
                is not None
            )
        assert _checkpoint(factory, version).processed_event_count == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_worker_crash_after_projection_commit_redelivery_is_receipt_idempotent() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3105
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        aggregate_id = uuid4()
        event = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=1,
            ordinal=50,
            available_at=BASE,
        )
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        claimed_at = BASE + timedelta(minutes=2)
        claim = repository.claim_batch(
            worker_id="pr31-crashed-after-projection",
            now=claimed_at,
            batch_size=1,
            lease_seconds=1,
            max_attempts=3,
        )[0]

        # Projection + receipt commit, then the process dies before mark_delivered.
        _ProjectionPublisher(store, version).publish(claim.envelope)
        assert _checkpoint(factory, version).processed_event_count == 1

        recovered = _worker(
            factory,
            store,
            version,
            worker_id="pr31-recovery-after-projection",
        ).run_once(now=claimed_at + timedelta(seconds=2))
        assert recovered.delivered == 1

        with factory() as session:
            row = session.get(OutboxEventRow, event.event_id)
            assert row is not None
            assert row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert row.publish_attempts == 2
            assert (
                session.get(OutboxConsumerReceiptRow, (consumer_name(version), event.event_id))
                is not None
            )
        assert _checkpoint(factory, version).processed_event_count == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.regression
def test_gap_event_can_poison_then_requeue_after_predecessor_and_converge() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 3106
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        aggregate_id = uuid4()
        t0 = BASE + timedelta(minutes=3)

        second = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=2,
            ordinal=60,
            available_at=t0,
        )
        first = _seed_airport_event(
            factory,
            aggregate_id=aggregate_id,
            aggregate_version=1,
            ordinal=61,
            available_at=t0 + timedelta(seconds=3),
        )
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        worker = _worker(
            factory,
            store,
            version,
            worker_id="pr31-gap-poison",
            max_attempts=2,
        )

        first_failure = worker.run_once(now=t0)
        assert first_failure.retried == 1
        second_failure = worker.run_once(now=t0 + timedelta(seconds=1))
        assert second_failure.poisoned == 1

        predecessor = worker.run_once(now=t0 + timedelta(seconds=3))
        assert predecessor.delivered == 1

        with factory() as session:
            poisoned = session.get(OutboxEventRow, second.event_id)
            assert poisoned is not None
            assert poisoned.delivery_status == OutboxDeliveryStatus.POISONED.value

        assert repository.requeue_poison(
            event_id=second.event_id,
            available_at=t0 + timedelta(seconds=4),
        )
        successor = worker.run_once(now=t0 + timedelta(seconds=4))
        assert successor.delivered == 1

        with factory() as session:
            cursor = session.get(
                GraphAggregateCursorRow,
                (PROJECTION_NAME, version, "airport", aggregate_id),
            )
            first_row = session.get(OutboxEventRow, first.event_id)
            second_row = session.get(OutboxEventRow, second.event_id)
            assert cursor is not None
            assert cursor.last_aggregate_version == 2
            assert first_row is not None
            assert first_row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert second_row is not None
            assert second_row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert second_row.publish_attempts == 3
            assert second_row.delivery_attempts == 1
        assert _checkpoint(factory, version).processed_event_count == 2
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.regression
def test_full_rebuild_from_zero_converges_to_identical_digest() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    first_version = 3190
    second_version = 3191
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        first = store.rebuild(first_version, now=BASE + timedelta(days=1))
        second = store.rebuild(second_version, now=BASE + timedelta(days=1))

        assert first.ok, first.issues
        assert second.ok, second.issues
        assert first.reference_digest == first.persisted_digest
        assert second.reference_digest == second.persisted_digest
        assert first.persisted_digest == second.persisted_digest
        assert first.event_count == second.event_count
        assert first.receipt_count == second.receipt_count
        assert first.checkpoint_count == second.checkpoint_count
    finally:
        engine.dispose()
