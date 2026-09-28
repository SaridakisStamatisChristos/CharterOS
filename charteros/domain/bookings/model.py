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


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


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
        version: int = 0,
    ) -> None:
        super().__init__(booking_id, version=version)
        self.mission_id = mission_id
        self.accepted_quote_id = accepted_quote_id
        self.operator_id = operator_id
        self.aircraft_id = aircraft_id
        self.state = BookingState(state)
        self.created_at = _utc(created_at, field_name="created_at")

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
        )
        booking._record_event(
            "BOOKING_CREATED",
            {
                "mission_id": str(booking.mission_id),
                "accepted_quote_id": str(booking.accepted_quote_id),
                "operator_id": str(booking.operator_id),
                "aircraft_id": str(booking.aircraft_id),
                "state": booking.state.value,
                "created_at": booking.created_at.isoformat().replace("+00:00", "Z"),
            },
            correlation_id=correlation_id,
            occurred_at=booking.created_at,
        )
        return booking
