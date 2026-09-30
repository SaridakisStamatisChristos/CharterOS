from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import ClassVar

from charteros.domain.shared.events import DomainEvent
from charteros.domain.shared.exceptions import (
    DomainValidationError,
    OptimisticConcurrencyError,
)
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId


class AggregateRoot[IdT: TypedId]:
    """Base aggregate with monotonic optimistic versioning and pending events."""

    aggregate_type: ClassVar[str] = ""

    def __init__(self, aggregate_id: IdT, *, version: int = 0) -> None:
        if not isinstance(aggregate_id, TypedId):
            raise DomainValidationError("aggregate_id must be a TypedId")
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise DomainValidationError("aggregate version must be a non-negative integer")
        if not self.aggregate_type or self.aggregate_type.strip() != self.aggregate_type:
            raise DomainValidationError(
                "aggregate subclasses must define a non-empty canonical aggregate_type"
            )
        self._id = aggregate_id
        self._version = version
        self._pending_events: list[DomainEvent] = []

    @property
    def id(self) -> IdT:
        return self._id

    @property
    def version(self) -> int:
        return self._version

    @property
    def pending_events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._pending_events)

    def assert_version(self, expected_version: int) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise DomainValidationError("expected version must be a non-negative integer")
        if expected_version != self._version:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version}, actual {self._version}"
            )

    def collect_events(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._pending_events)
        self._pending_events.clear()
        return events

    def _record_event(
        self,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        recorded_at: datetime,
        occurred_at: datetime | None = None,
        event_version: int = 1,
        actor_id: TypedId | None = None,
        correlation_id: CorrelationId | None = None,
        causation_id: EventId | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_id=EventId.new(),
            aggregate_type=self.aggregate_type,
            aggregate_id=self._id,
            aggregate_version=self._version + 1,
            event_type=event_type,
            event_version=event_version,
            occurred_at=recorded_at if occurred_at is None else occurred_at,
            recorded_at=recorded_at,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload or {},
        )
        self._version += 1
        self._pending_events.append(event)
        return event
