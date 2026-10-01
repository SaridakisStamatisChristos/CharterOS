from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event

from charteros.application.outbox import (
    OutboxDeliveryRepository,
    OutboxLeaseLostError,
    OutboxPublisher,
    retry_delay_seconds,
)
from charteros.observability import get_operational_metrics
from charteros.shared.logging import get_logger


@dataclass(frozen=True, slots=True)
class OutboxWorkerConfig:
    batch_size: int
    lease_seconds: int
    max_attempts: int
    backoff_base_seconds: int
    backoff_max_seconds: int
    poll_interval_seconds: float


@dataclass(frozen=True, slots=True)
class OutboxRunResult:
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    poisoned: int = 0
    lease_lost: int = 0


class OutboxWorker:
    def __init__(
        self,
        *,
        repository: OutboxDeliveryRepository,
        publisher: OutboxPublisher,
        worker_id: str,
        config: OutboxWorkerConfig,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self._repository = repository
        self._publisher = publisher
        self._worker_id = worker_id
        self._config = config
        self._logger = get_logger(__name__)
        self._metrics = get_operational_metrics()

    def run_once(self, *, now: datetime | None = None) -> OutboxRunResult:
        operation_time = _utc(now or datetime.now(UTC))
        claims = self._repository.claim_batch(
            worker_id=self._worker_id,
            now=operation_time,
            batch_size=self._config.batch_size,
            lease_seconds=self._config.lease_seconds,
            max_attempts=self._config.max_attempts,
        )
        delivered = 0
        retried = 0
        poisoned = 0
        lease_lost = 0

        for claim in claims:
            try:
                self._publisher.publish(claim.envelope)
            except Exception as exc:
                poison = claim.attempt >= self._config.max_attempts
                retry_at = operation_time + timedelta(
                    seconds=retry_delay_seconds(
                        attempt=claim.attempt,
                        base_seconds=self._config.backoff_base_seconds,
                        max_seconds=self._config.backoff_max_seconds,
                    )
                )
                try:
                    self._repository.mark_failed(
                        event_id=claim.envelope.event_id,
                        lease_token=claim.lease_token,
                        failed_at=operation_time,
                        error=_error_text(exc),
                        retry_at=retry_at,
                        poison=poison,
                    )
                except OutboxLeaseLostError:
                    lease_lost += 1
                    self._log_lease_lost(claim.envelope.event_id)
                else:
                    if poison:
                        poisoned += 1
                    else:
                        retried += 1
                continue

            try:
                self._repository.mark_delivered(
                    event_id=claim.envelope.event_id,
                    lease_token=claim.lease_token,
                    delivered_at=operation_time,
                )
            except OutboxLeaseLostError:
                lease_lost += 1
                self._log_lease_lost(claim.envelope.event_id)
            else:
                delivered += 1
                self._metrics.outbox_delivery_latency(
                    max(
                        0.0,
                        (operation_time - claim.envelope.recorded_at.astimezone(UTC)).total_seconds(),
                    )
                )

        result = OutboxRunResult(
            claimed=len(claims),
            delivered=delivered,
            retried=retried,
            poisoned=poisoned,
            lease_lost=lease_lost,
        )
        self._metrics.outbox_result(
            claimed=result.claimed,
            delivered=result.delivered,
            retried=result.retried,
            poisoned=result.poisoned,
            lease_lost=result.lease_lost,
        )
        return result

    def run_forever(self, stop_event: Event) -> None:
        while not stop_event.is_set():
            result = self.run_once()
            if result.claimed == 0:
                stop_event.wait(self._config.poll_interval_seconds)

    def _log_lease_lost(self, event_id: object) -> None:
        self._logger.warning(
            "outbox_delivery_lease_lost",
            extra={
                "event": "outbox_delivery_lease_lost",
                "event_id": str(event_id),
                "worker_id": self._worker_id,
            },
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox worker timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _error_text(exc: Exception) -> str:
    value = f"{type(exc).__name__}: {exc}".strip()
    return value[:4000]
