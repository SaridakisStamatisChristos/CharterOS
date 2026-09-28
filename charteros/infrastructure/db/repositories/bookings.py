from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import Booking, BookingId, BookingState
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.infrastructure.db.models.bookings import BookingRow


def _to_domain(row: BookingRow) -> Booking:
    return Booking(
        BookingId(row.id),
        mission_id=MissionId(row.mission_id),
        accepted_quote_id=QuoteId(row.accepted_quote_id),
        operator_id=OperatorId(row.operator_id),
        aircraft_id=AircraftId(row.aircraft_id),
        state=BookingState(row.state),
        created_at=row.created_at,
        version=row.version,
    )


class SqlAlchemyBookingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, booking: Booking) -> None:
        self._session.add(
            BookingRow(
                id=booking.id.value,
                version=booking.version,
                mission_id=booking.mission_id.value,
                accepted_quote_id=booking.accepted_quote_id.value,
                operator_id=booking.operator_id.value,
                aircraft_id=booking.aircraft_id.value,
                state=booking.state.value,
                created_at=booking.created_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(
                "mission already has a booking or quote is already attached to a booking"
            ) from exc

    def get(self, booking_id: BookingId) -> Booking | None:
        row = self._session.get(BookingRow, booking_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_mission(self, mission_id: MissionId) -> Booking | None:
        row = self._session.scalar(
            select(BookingRow).where(BookingRow.mission_id == mission_id.value)
        )
        return _to_domain(row) if row is not None else None
