from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.aircraft import AircraftId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class BookingId(TypedId):
    __slots__ = ()


class BookingState(StrEnum):
    PENDING_CONTRACT = "pending_contract"
    CONTRACTED = "contracted"
    PAYMENT_PENDING = "payment_pending"
    CONFIRMED = "confirmed"
    PRE_OPERATION = "pre_operation"
    OPERATING = "operating"
    COMPLETED = "completed"
    RECONCILED = "reconciled"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Booking(AggregateRoot[BookingId]):
    aggregate_type = "booking"

    def __init__(
        self,
        booking_id: BookingId,
        *,
        mission_id: MissionId,
        accepted_quote_id: QuoteId,
        operator_id: OperatorId,
        aircraft_id: AircraftId,
        state: BookingState,
        created_at: datetime,
        state_changed_at: datetime | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(booking_id, version=version)
        self.mission_id = mission_id
        self.accepted_quote_id = accepted_quote_id
        self.operator_id = operator_id
        self.aircraft_id = aircraft_id
        self.state = BookingState(state)
        self.created_at = _utc(created_at, field_name="created_at")
        self.state_changed_at = _utc(
            state_changed_at if state_changed_at is not None else created_at,
            field_name="state_changed_at",
        )
        if self.state_changed_at < self.created_at:
            raise DomainValidationError("state_changed_at cannot precede booking creation")

    @classmethod
    def create(
        cls,
        *,
        mission_id: MissionId,
        accepted_quote_id: QuoteId,
        operator_id: OperatorId,
        aircraft_id: AircraftId,
        created_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Booking:
        booking = cls(
            BookingId.new(),
            mission_id=mission_id,
            accepted_quote_id=accepted_quote_id,
            operator_id=operator_id,
            aircraft_id=aircraft_id,
            state=BookingState.PENDING_CONTRACT,
            created_at=created_at,
            state_changed_at=created_at,
        )
        booking._record_event(
            "BOOKING_CREATED",
            {
                "mission_id": str(booking.mission_id),
                "accepted_quote_id": str(booking.accepted_quote_id),
                "operator_id": str(booking.operator_id),
                "aircraft_id": str(booking.aircraft_id),
                "state": booking.state.value,
                "created_at": _iso(booking.created_at),
            },
            correlation_id=correlation_id,
            occurred_at=booking.created_at,
        )
        return booking

    def mark_contracted(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.PENDING_CONTRACT,
            target=BookingState.CONTRACTED,
            event_type="BOOKING_CONTRACTED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def mark_payment_pending(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.CONTRACTED,
            target=BookingState.PAYMENT_PENDING,
            event_type="BOOKING_PAYMENT_PENDING",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def confirm(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.PAYMENT_PENDING,
            target=BookingState.CONFIRMED,
            event_type="BOOKING_CONFIRMED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def enter_pre_operation(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.CONFIRMED,
            target=BookingState.PRE_OPERATION,
            event_type="BOOKING_PRE_OPERATION",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def start_operation(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.PRE_OPERATION,
            target=BookingState.OPERATING,
            event_type="BOOKING_OPERATING",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def complete(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.OPERATING,
            target=BookingState.COMPLETED,
            event_type="BOOKING_COMPLETED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def reconcile(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._transition(
            expected=BookingState.COMPLETED,
            target=BookingState.RECONCILED,
            event_type="BOOKING_RECONCILED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def _transition(
        self,
        *,
        expected: BookingState,
        target: BookingState,
        event_type: str,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None,
    ) -> None:
        if self.state is not expected:
            raise DomainValidationError(
                f"booking must be {expected.value} before transition to {target.value}"
            )
        when = _utc(transitioned_at, field_name="transitioned_at")
        if when < self.state_changed_at:
            raise DomainValidationError("transitioned_at cannot precede the current booking state")

        previous = self.state
        self.state = target
        self.state_changed_at = when
        self._record_event(
            event_type,
            {
                "from_state": previous.value,
                "to_state": target.value,
                "transitioned_at": _iso(when),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )
