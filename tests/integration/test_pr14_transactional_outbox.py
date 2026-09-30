import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.outbox import OutboxDeliveryStatus, OutboxEnvelope, OutboxLeaseLostError
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import IdempotencyRecordRow, OutboxEventRow
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.outbox import (
    SqlAlchemyIdempotentConsumerRunner,
    SqlAlchemyOutboxDeliveryRepository,
)
from charteros.outbox import OutboxWorker, OutboxWorkerConfig
from charteros.shared.config import Settings

BASE = datetime(2000, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _seed_event(
    factory: sessionmaker[Session],
    *,
    ordinal: int,
    available_at: datetime | None = None,
) -> UUID:
    event_id = uuid4()
    recorded_at = BASE + timedelta(seconds=ordinal)
    with factory.begin() as session:
        session.add(
            OutboxEventRow(
                event_id=event_id,
                aggregate_type="pr14_test",
                aggregate_id=uuid4(),
                aggregate_version=1,
                event_type="PR14_TEST_EVENT",
                event_version=1,
                occurred_at=recorded_at,
                recorded_at=recorded_at,
                actor_id=None,
                correlation_id=None,
                causation_id=None,
                canonical_json='{"event_type":"PR14_TEST_EVENT"}',
                delivery_status=OutboxDeliveryStatus.PENDING.value,
                available_at=available_at or recorded_at,
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
    return event_id


def _config(*, max_attempts: int = 3) -> OutboxWorkerConfig:
    return OutboxWorkerConfig(
        batch_size=1,
        lease_seconds=5,
        max_attempts=max_attempts,
        backoff_base_seconds=2,
        backoff_max_seconds=30,
        poll_interval_seconds=0.05,
    )


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[UUID] = []

    def publish(self, envelope: OutboxEnvelope) -> None:
        self.events.append(envelope.event_id)
        if self.fail:
            raise RuntimeError("synthetic delivery failure")


@pytest.mark.integration
def test_outbox_worker_delivers_and_persists_delivery_metadata() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(factory, ordinal=1)
        publisher = RecordingPublisher()
        worker = OutboxWorker(
            repository=SqlAlchemyOutboxDeliveryRepository(factory),
            publisher=publisher,
            worker_id="pr14-worker-success",
            config=_config(),
        )

        result = worker.run_once(now=BASE + timedelta(minutes=1))
        assert result.delivered == 1
        assert publisher.events == [event_id]

        with factory() as session:
            row = session.get(OutboxEventRow, event_id)
            assert row is not None
            assert row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert row.publish_attempts == 1
            assert row.delivery_attempts == 1
            assert row.published_at == BASE + timedelta(minutes=1)
            assert row.lease_owner is None
            assert row.lease_token is None
            assert row.lease_expires_at is None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_retry_backoff_poison_and_explicit_requeue_preserve_lifetime_attempts() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(factory, ordinal=2)
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        failing_worker = OutboxWorker(
            repository=repository,
            publisher=RecordingPublisher(fail=True),
            worker_id="pr14-worker-fail",
            config=_config(max_attempts=2),
        )
        first_time = BASE + timedelta(minutes=2)

        first = failing_worker.run_once(now=first_time)
        assert first.retried == 1
        assert failing_worker.run_once(now=first_time + timedelta(seconds=1)).claimed == 0

        second = failing_worker.run_once(now=first_time + timedelta(seconds=2))
        assert second.poisoned == 1
        assert failing_worker.run_once(now=first_time + timedelta(seconds=3)).claimed == 0

        with factory() as session:
            poisoned = session.get(OutboxEventRow, event_id)
            assert poisoned is not None
            assert poisoned.delivery_status == OutboxDeliveryStatus.POISONED.value
            assert poisoned.publish_attempts == 2
            assert poisoned.delivery_attempts == 2
            assert poisoned.poisoned_at == first_time + timedelta(seconds=2)

        assert repository.requeue_poison(
            event_id=event_id,
            available_at=first_time + timedelta(seconds=4),
        )

        success_publisher = RecordingPublisher()
        success_worker = OutboxWorker(
            repository=repository,
            publisher=success_publisher,
            worker_id="pr14-worker-requeued",
            config=_config(max_attempts=2),
        )
        final = success_worker.run_once(now=first_time + timedelta(seconds=4))
        assert final.delivered == 1
        assert success_publisher.events == [event_id]

        with factory() as session:
            delivered = session.get(OutboxEventRow, event_id)
            assert delivered is not None
            assert delivered.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            assert delivered.publish_attempts == 3
            assert delivered.delivery_attempts == 1
            assert delivered.poisoned_at is None
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_expired_lease_reclaim_fences_stale_worker() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(factory, ordinal=3)
        first_repository = SqlAlchemyOutboxDeliveryRepository(factory)
        second_repository = SqlAlchemyOutboxDeliveryRepository(factory)
        first_claim = first_repository.claim_batch(
            worker_id="worker-old",
            now=BASE + timedelta(minutes=3),
            batch_size=1,
            lease_seconds=1,
            max_attempts=3,
        )[0]
        second_claim = second_repository.claim_batch(
            worker_id="worker-new",
            now=BASE + timedelta(minutes=3, seconds=2),
            batch_size=1,
            lease_seconds=5,
            max_attempts=3,
        )[0]

        assert second_claim.envelope.event_id == event_id
        assert second_claim.lease_token != first_claim.lease_token
        assert second_claim.attempt == 2

        with pytest.raises(OutboxLeaseLostError):
            first_repository.mark_delivered(
                event_id=event_id,
                lease_token=first_claim.lease_token,
                delivered_at=BASE + timedelta(minutes=3, seconds=2),
            )

        second_repository.mark_delivered(
            event_id=event_id,
            lease_token=second_claim.lease_token,
            delivered_at=BASE + timedelta(minutes=3, seconds=2),
        )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_skip_locked_claimers_partition_due_work() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        first_id = _seed_event(factory, ordinal=4)
        second_id = _seed_event(factory, ordinal=5)
        barrier = Barrier(2)

        def claim(worker_id: str) -> UUID:
            barrier.wait()
            repository = SqlAlchemyOutboxDeliveryRepository(factory)
            claims = repository.claim_batch(
                worker_id=worker_id,
                now=BASE + timedelta(minutes=5),
                batch_size=1,
                lease_seconds=30,
                max_attempts=3,
            )
            assert len(claims) == 1
            return claims[0].envelope.event_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(claim, "worker-a")
            b = executor.submit(claim, "worker-b")
            claimed = {a.result(), b.result()}

        assert claimed == {first_id, second_id}
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_idempotent_consumer_runner_executes_concurrent_duplicate_once() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(
            factory,
            ordinal=6,
            available_at=BASE + timedelta(days=36500),
        )
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        envelope = repository.get_envelope(event_id)
        assert envelope is not None

        runner = SqlAlchemyIdempotentConsumerRunner(factory)
        barrier = Barrier(2)
        guard = Lock()
        calls: list[UUID] = []

        def handler(_session: Session, item: OutboxEnvelope) -> None:
            with guard:
                calls.append(item.event_id)

        def consume() -> bool:
            barrier.wait()
            return runner.consume(
                consumer_name="pr15-graph-preview",
                consumer_version=1,
                envelope=envelope,
                processed_at=BASE + timedelta(minutes=6),
                handler=handler,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(consume)
            second = executor.submit(consume)
            results = sorted((first.result(), second.result()))

        assert results == [False, True]
        assert calls == [event_id]
        with factory() as session:
            receipts: Sequence[OutboxConsumerReceiptRow] = session.scalars(
                select(OutboxConsumerReceiptRow).where(
                    OutboxConsumerReceiptRow.event_id == event_id
                )
            ).all()
            assert len(receipts) == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_consumer_commit_survives_lost_delivery_acknowledgement() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(factory, ordinal=43)
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        first_claim = repository.claim_batch(
            worker_id="pr43-worker-before-ack-loss",
            now=BASE + timedelta(minutes=43),
            batch_size=1,
            lease_seconds=1,
            max_attempts=3,
        )[0]
        runner = SqlAlchemyIdempotentConsumerRunner(factory)
        handler_calls: list[UUID] = []

        def handler(session: Session, envelope: OutboxEnvelope) -> None:
            handler_calls.append(envelope.event_id)
            session.add(
                IdempotencyRecordRow(
                    scope="pr43:consumer-side-effect",
                    key=str(envelope.event_id),
                    request_hash="b" * 64,
                    status_code=200,
                    response_body={"effect": "committed"},
                )
            )

        assert runner.consume(
            consumer_name="pr43-durable-consumer",
            consumer_version=1,
            envelope=first_claim.envelope,
            processed_at=BASE + timedelta(minutes=43),
            handler=handler,
        )

        # Simulate process death after consumer commit but before delivery acknowledgement.
        reclaimed = repository.claim_batch(
            worker_id="pr43-worker-after-ack-loss",
            now=BASE + timedelta(minutes=43, seconds=2),
            batch_size=1,
            lease_seconds=5,
            max_attempts=3,
        )
        assert len(reclaimed) == 1
        second_claim = reclaimed[0]
        assert second_claim.envelope.event_id == event_id
        assert second_claim.lease_token != first_claim.lease_token

        # Redelivery is safe because the durable receipt and side effect committed together.
        assert not runner.consume(
            consumer_name="pr43-durable-consumer",
            consumer_version=1,
            envelope=second_claim.envelope,
            processed_at=BASE + timedelta(minutes=43, seconds=2),
            handler=handler,
        )
        assert handler_calls == [event_id]

        with pytest.raises(OutboxLeaseLostError):
            repository.mark_delivered(
                event_id=event_id,
                lease_token=first_claim.lease_token,
                delivered_at=BASE + timedelta(minutes=43, seconds=2),
            )

        repository.mark_delivered(
            event_id=event_id,
            lease_token=second_claim.lease_token,
            delivered_at=BASE + timedelta(minutes=43, seconds=2),
        )

        with factory() as session:
            row = session.get(OutboxEventRow, event_id)
            assert row is not None
            assert row.delivery_status == OutboxDeliveryStatus.DELIVERED.value
            side_effect = session.get(
                IdempotencyRecordRow,
                ("pr43:consumer-side-effect", str(event_id)),
            )
            assert side_effect is not None
            assert side_effect.response_body == {"effect": "committed"}
            receipt = session.get(
                OutboxConsumerReceiptRow,
                ("pr43-durable-consumer", event_id),
            )
            assert receipt is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_expired_final_attempt_is_poisoned_without_reclaim_loop() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        event_id = _seed_event(factory, ordinal=44)
        repository = SqlAlchemyOutboxDeliveryRepository(factory)
        claim = repository.claim_batch(
            worker_id="pr43-final-attempt",
            now=BASE + timedelta(minutes=44),
            batch_size=1,
            lease_seconds=1,
            max_attempts=1,
        )
        assert len(claim) == 1
        assert claim[0].attempt == 1

        # Process dies without ack/failure. Once this final lease expires, the next claim pass
        # deterministically poisons it instead of creating an unbounded retry loop.
        assert (
            repository.claim_batch(
                worker_id="pr43-after-final-expiry",
                now=BASE + timedelta(minutes=44, seconds=2),
                batch_size=1,
                lease_seconds=1,
                max_attempts=1,
            )
            == ()
        )
        assert (
            repository.claim_batch(
                worker_id="pr43-after-poison",
                now=BASE + timedelta(minutes=44, seconds=3),
                batch_size=1,
                lease_seconds=1,
                max_attempts=1,
            )
            == ()
        )

        with factory() as session:
            row = session.get(OutboxEventRow, event_id)
            assert row is not None
            assert row.delivery_status == OutboxDeliveryStatus.POISONED.value
            assert row.delivery_attempts == 1
            assert row.poisoned_at == BASE + timedelta(minutes=44, seconds=2)
            assert row.last_error == "delivery lease expired after the final allowed attempt"
    finally:
        engine.dispose()
