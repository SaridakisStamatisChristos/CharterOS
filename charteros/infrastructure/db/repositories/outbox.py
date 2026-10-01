from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.outbox import (
    OutboxClaim,
    OutboxDeliveryStatus,
    OutboxEnvelope,
    OutboxLeaseLostError,
)
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.observability import get_operational_metrics

ConsumerHandler = Callable[[Session, OutboxEnvelope], None]


class SqlAlchemyOutboxDeliveryRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[OutboxClaim, ...]:
        when = _utc(now)
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        with self._session_factory.begin() as session:
            self._poison_exhausted_expired_leases(
                session,
                now=when,
                max_attempts=max_attempts,
            )
            due = or_(
                and_(
                    OutboxEventRow.delivery_status.in_(
                        (
                            OutboxDeliveryStatus.PENDING.value,
                            OutboxDeliveryStatus.RETRY.value,
                        )
                    ),
                    OutboxEventRow.available_at <= when,
                ),
                and_(
                    OutboxEventRow.delivery_status == OutboxDeliveryStatus.IN_FLIGHT.value,
                    OutboxEventRow.lease_expires_at <= when,
                ),
            )
            rows = (
                session.scalars(
                    select(OutboxEventRow)
                    .where(
                        due,
                        OutboxEventRow.delivery_attempts < max_attempts,
                    )
                    .order_by(OutboxEventRow.recorded_at, OutboxEventRow.event_id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                .unique()
                .all()
            )

            claims: list[OutboxClaim] = []
            for row in rows:
                token = uuid4()
                row.delivery_status = OutboxDeliveryStatus.IN_FLIGHT.value
                row.publish_attempts += 1
                row.delivery_attempts += 1
                row.last_attempt_at = when
                row.lease_owner = worker_id
                row.lease_token = token
                row.lease_expires_at = when + timedelta(seconds=lease_seconds)
                claims.append(
                    OutboxClaim(
                        envelope=_to_envelope(row),
                        lease_token=token,
                        attempt=row.delivery_attempts,
                    )
                )
            session.flush()
            return tuple(claims)

    def mark_delivered(
        self,
        *,
        event_id: UUID,
        lease_token: UUID,
        delivered_at: datetime,
    ) -> None:
        when = _utc(delivered_at)
        with self._session_factory.begin() as session:
            updated_id = session.scalar(
                update(OutboxEventRow)
                .where(
                    OutboxEventRow.event_id == event_id,
                    OutboxEventRow.delivery_status == OutboxDeliveryStatus.IN_FLIGHT.value,
                    OutboxEventRow.lease_token == lease_token,
                )
                .values(
                    delivery_status=OutboxDeliveryStatus.DELIVERED.value,
                    published_at=when,
                    available_at=when,
                    last_error=None,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    poisoned_at=None,
                )
                .returning(OutboxEventRow.event_id)
            )
            if updated_id is None:
                raise OutboxLeaseLostError(f"delivery lease lost for event {event_id}")

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
        failed_when = _utc(failed_at)
        retry_when = _utc(retry_at)
        if retry_when < failed_when:
            raise ValueError("retry_at cannot precede failed_at")
        error_text = error.strip()[:4000] or "unknown delivery failure"
        status = OutboxDeliveryStatus.POISONED.value if poison else OutboxDeliveryStatus.RETRY.value
        with self._session_factory.begin() as session:
            updated_id = session.scalar(
                update(OutboxEventRow)
                .where(
                    OutboxEventRow.event_id == event_id,
                    OutboxEventRow.delivery_status == OutboxDeliveryStatus.IN_FLIGHT.value,
                    OutboxEventRow.lease_token == lease_token,
                )
                .values(
                    delivery_status=status,
                    available_at=failed_when if poison else retry_when,
                    last_error=error_text,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    poisoned_at=failed_when if poison else None,
                )
                .returning(OutboxEventRow.event_id)
            )
            if updated_id is None:
                raise OutboxLeaseLostError(f"delivery lease lost for event {event_id}")

    def requeue_poison(self, *, event_id: UUID, available_at: datetime) -> bool:
        when = _utc(available_at)
        with self._session_factory.begin() as session:
            updated_id = session.scalar(
                update(OutboxEventRow)
                .where(
                    OutboxEventRow.event_id == event_id,
                    OutboxEventRow.delivery_status == OutboxDeliveryStatus.POISONED.value,
                )
                .values(
                    delivery_status=OutboxDeliveryStatus.RETRY.value,
                    available_at=when,
                    poisoned_at=None,
                    delivery_attempts=0,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
                .returning(OutboxEventRow.event_id)
            )
            return updated_id is not None

    def get_envelope(self, event_id: UUID) -> OutboxEnvelope | None:
        with self._session_factory() as session:
            row = session.get(OutboxEventRow, event_id)
            return _to_envelope(row) if row is not None else None

    def _poison_exhausted_expired_leases(
        self,
        session: Session,
        *,
        now: datetime,
        max_attempts: int,
    ) -> None:
        session.execute(
            update(OutboxEventRow)
            .where(
                OutboxEventRow.delivery_status == OutboxDeliveryStatus.IN_FLIGHT.value,
                OutboxEventRow.lease_expires_at <= now,
                OutboxEventRow.delivery_attempts >= max_attempts,
            )
            .values(
                delivery_status=OutboxDeliveryStatus.POISONED.value,
                available_at=now,
                last_error="delivery lease expired after the final allowed attempt",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                poisoned_at=now,
            )
        )


class SqlAlchemyIdempotentConsumerRunner:
    """Atomically executes a database consumer and records its durable deduplication receipt."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def consume(
        self,
        *,
        consumer_name: str,
        consumer_version: int,
        envelope: OutboxEnvelope,
        processed_at: datetime,
        handler: ConsumerHandler,
    ) -> bool:
        name = consumer_name.strip()
        if not name or len(name) > 128:
            raise ValueError("consumer_name must contain 1 to 128 characters")
        if consumer_version < 1:
            raise ValueError("consumer_version must be positive")
        when = _utc(processed_at)

        with self._session_factory.begin() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:outbox_consumer_lock_key, 0))"
                    ),
                    {"outbox_consumer_lock_key": (f"{name}:{envelope.event_id}")},
                )
            receipt = session.get(
                OutboxConsumerReceiptRow,
                (name, envelope.event_id),
            )
            if receipt is not None:
                get_operational_metrics().outbox_dedupe_hit()
                return False

            handler(session, envelope)
            session.add(
                OutboxConsumerReceiptRow(
                    consumer_name=name,
                    event_id=envelope.event_id,
                    consumer_version=consumer_version,
                    processed_at=when,
                )
            )
            session.flush()
            return True


def _to_envelope(row: OutboxEventRow) -> OutboxEnvelope:
    return OutboxEnvelope(
        event_id=row.event_id,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        event_type=row.event_type,
        event_version=row.event_version,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        actor_id=row.actor_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        canonical_json=row.canonical_json,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox repository timestamps must be timezone-aware")
    return value.astimezone(UTC)
