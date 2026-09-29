from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from charteros.application.outbox import (
    OutboxClaim,
    OutboxEnvelope,
    OutboxLeaseLostError,
    retry_delay_seconds,
)
from charteros.outbox import OutboxWorker, OutboxWorkerConfig

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _envelope() -> OutboxEnvelope:
    return OutboxEnvelope(
        event_id=uuid4(),
        aggregate_type="booking",
        aggregate_id=uuid4(),
        aggregate_version=1,
        event_type="BOOKING_CREATED",
        event_version=1,
        occurred_at=NOW,
        recorded_at=NOW,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json="{}",
    )


class FakeRepository:
    def __init__(self, claims: list[OutboxClaim]) -> None:
        self.claims = claims
        self.delivered: list[UUID] = []
        self.failed: list[tuple[UUID, bool, datetime]] = []

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[OutboxClaim, ...]:
        claims = tuple(self.claims[:batch_size])
        self.claims = self.claims[batch_size:]
        return claims

    def mark_delivered(
        self,
        *,
        event_id: UUID,
        lease_token: UUID,
        delivered_at: datetime,
    ) -> None:
        self.delivered.append(event_id)

    def mark_failed(
        self,
        *,
        event_id: UUID,
        lease_token: UUID,
        failed_at: datetime,
        error: str,
        retry_at: datetime,
        poison: bool,
    ) -> None:
        self.failed.append((event_id, poison, retry_at))

    def requeue_poison(self, *, event_id: UUID, available_at: datetime) -> bool:
        return False


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[UUID] = []

    def publish(self, envelope: OutboxEnvelope) -> None:
        self.events.append(envelope.event_id)
        if self.fail:
            raise RuntimeError("synthetic delivery failure")


def _config(*, max_attempts: int = 3) -> OutboxWorkerConfig:
    return OutboxWorkerConfig(
        batch_size=10,
        lease_seconds=30,
        max_attempts=max_attempts,
        backoff_base_seconds=2,
        backoff_max_seconds=30,
        poll_interval_seconds=0.1,
    )


def test_retry_backoff_is_deterministic_and_capped() -> None:
    assert retry_delay_seconds(attempt=1, base_seconds=2, max_seconds=30) == 2
    assert retry_delay_seconds(attempt=2, base_seconds=2, max_seconds=30) == 4
    assert retry_delay_seconds(attempt=5, base_seconds=2, max_seconds=30) == 30
    assert retry_delay_seconds(attempt=50, base_seconds=2, max_seconds=30) == 30


def test_worker_marks_successful_delivery() -> None:
    envelope = _envelope()
    claim = OutboxClaim(envelope=envelope, lease_token=uuid4(), attempt=1)
    repository = FakeRepository([claim])
    publisher = RecordingPublisher()
    worker = OutboxWorker(
        repository=repository,
        publisher=publisher,
        worker_id="worker-a",
        config=_config(),
    )

    result = worker.run_once(now=NOW)

    assert result.claimed == 1
    assert result.delivered == 1
    assert result.retried == result.poisoned == result.lease_lost == 0
    assert publisher.events == [envelope.event_id]
    assert repository.delivered == [envelope.event_id]


def test_worker_retries_then_poison_marks_final_attempt() -> None:
    first = _envelope()
    retry_claim = OutboxClaim(envelope=first, lease_token=uuid4(), attempt=1)
    retry_repository = FakeRepository([retry_claim])
    retry_worker = OutboxWorker(
        repository=retry_repository,
        publisher=RecordingPublisher(fail=True),
        worker_id="worker-retry",
        config=_config(max_attempts=2),
    )

    retry_result = retry_worker.run_once(now=NOW)
    assert retry_result.retried == 1
    assert retry_repository.failed == [(first.event_id, False, NOW + timedelta(seconds=2))]

    final = _envelope()
    poison_claim = OutboxClaim(envelope=final, lease_token=uuid4(), attempt=2)
    poison_repository = FakeRepository([poison_claim])
    poison_worker = OutboxWorker(
        repository=poison_repository,
        publisher=RecordingPublisher(fail=True),
        worker_id="worker-poison",
        config=_config(max_attempts=2),
    )

    poison_result = poison_worker.run_once(now=NOW)
    assert poison_result.poisoned == 1
    assert poison_repository.failed[0][0] == final.event_id
    assert poison_repository.failed[0][1] is True


def test_stale_acknowledgement_is_counted_as_lease_loss() -> None:
    class LostLeaseRepository(FakeRepository):
        def mark_delivered(
            self,
            *,
            event_id: UUID,
            lease_token: UUID,
            delivered_at: datetime,
        ) -> None:
            raise OutboxLeaseLostError("lost")

    envelope = _envelope()
    repository = LostLeaseRepository(
        [OutboxClaim(envelope=envelope, lease_token=uuid4(), attempt=1)]
    )
    worker = OutboxWorker(
        repository=repository,
        publisher=RecordingPublisher(),
        worker_id="worker-stale",
        config=_config(),
    )

    result = worker.run_once(now=NOW)

    assert result.claimed == 1
    assert result.delivered == 0
    assert result.lease_lost == 1
