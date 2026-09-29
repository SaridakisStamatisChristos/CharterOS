from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class OutboxDeliveryStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    RETRY = "retry"
    DELIVERED = "delivered"
    POISONED = "poisoned"


@dataclass(frozen=True, slots=True)
class OutboxEnvelope:
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    canonical_json: str


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    envelope: OutboxEnvelope
    lease_token: UUID
    attempt: int


class OutboxLeaseLostError(RuntimeError):
    """Raised when a stale worker tries to mutate a delivery it no longer owns."""


class OutboxPublisher(Protocol):
    def publish(self, envelope: OutboxEnvelope) -> None: ...


class OutboxDeliveryRepository(Protocol):
    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[OutboxClaim, ...]: ...

    def mark_delivered(
        self,
        *,
        event_id: UUID,
        lease_token: UUID,
        delivered_at: datetime,
    ) -> None: ...

    def mark_failed(
        self,
        *,
        event_id: UUID,
        lease_token: UUID,
        failed_at: datetime,
        error: str,
        retry_at: datetime,
        poison: bool,
    ) -> None: ...

    def requeue_poison(self, *, event_id: UUID, available_at: datetime) -> bool: ...


def retry_delay_seconds(*, attempt: int, base_seconds: int, max_seconds: int) -> int:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    if base_seconds < 1:
        raise ValueError("base_seconds must be positive")
    if max_seconds < base_seconds:
        raise ValueError("max_seconds must be greater than or equal to base_seconds")
    exponent = min(attempt - 1, 30)
    return min(base_seconds * (1 << exponent), max_seconds)
