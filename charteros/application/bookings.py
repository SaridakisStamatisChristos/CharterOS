from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import DomainEventRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.domain.bookings import Booking, BookingId
from charteros.domain.missions import MissionStatus
from charteros.domain.quotes import QuoteId, QuoteStatus
from charteros.domain.rfqs import RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class BookingService:
    def __init__(
        self,
        *,
        bookings: BookingRepository,
        quotes: QuoteRepository,
        rfqs: RfqRepository,
        missions: MissionRepository,
        events: DomainEventRepository,
    ) -> None:
        self._bookings = bookings
        self._quotes = quotes
        self._rfqs = rfqs
        self._missions = missions
        self._events = events

    def accept_quote(
        self,
        *,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        accepted_at = _utc(now, field_name="now")

        observed = self._quotes.get(quote_id)
        if observed is None:
            raise EntityNotFoundError("quote does not exist")
        observed_rfq = self._rfqs.get(observed.rfq_id)
        if observed_rfq is None:
            raise EntityNotFoundError("quote RFQ does not exist")

        # The mission row is the serialization point for the award boundary. Quote submission and
        # revision take the same lock, so no fresh current quote can appear after this snapshot.
        mission = self._missions.get_for_update(observed_rfq.mission_id)
        if mission is None:
            raise EntityNotFoundError("quote mission does not exist")
        if mission.status not in (MissionStatus.SOURCING, MissionStatus.QUOTED):
            raise EntityConflictError("mission has already been awarded or is not awardable")
        if self._bookings.get_for_mission(mission.id) is not None:
            raise EntityConflictError("mission already has a booking")

        rfqs = self._rfqs.list_for_mission(mission.id)
        rfq_by_id = {rfq.id: rfq for rfq in rfqs}
        current_quotes = self._quotes.list_current_for_rfqs_for_update(
            tuple(rfq.id for rfq in rfqs)
        )
        target = next((quote for quote in current_quotes if quote.id == quote_id), None)
        if target is None:
            raise EntityConflictError("only the current submitted quote can be accepted")

        target_rfq = rfq_by_id.get(target.rfq_id)
        if target_rfq is None or target_rfq.status is not RfqStatus.QUOTED:
            raise EntityConflictError("accepted quote must belong to a quoted RFQ")
        if target.status is not QuoteStatus.SUBMITTED or not target.is_current:
            raise EntityConflictError("only the current submitted quote can be accepted")
        if accepted_at >= target.valid_until:
            raise EntityConflictError("expired quote cannot be accepted")

        booking = Booking.create(
            mission_id=mission.id,
            accepted_quote_id=target.id,
            operator_id=target_rfq.operator_id,
            aircraft_id=target.aircraft_id,
            created_at=accepted_at,
            correlation_id=correlation_id,
        )
        expected_mission_version = mission.version
        expected_quote_versions = {quote.id: quote.version for quote in current_quotes}

        target.accept(
            accepted_at=accepted_at,
            booking_id=str(booking.id),
            correlation_id=correlation_id,
        )
        for quote in current_quotes:
            if quote.id == target.id:
                continue
            quote.reject(
                rejected_at=accepted_at,
                accepted_quote_id=str(target.id),
                correlation_id=correlation_id,
            )

        mission.select_quote(
            quote_id=str(target.id),
            booking_id=str(booking.id),
            selected_at=accepted_at,
            correlation_id=correlation_id,
        )

        self._bookings.add(booking)
        for quote in current_quotes:
            self._quotes.save(quote, expected_version=expected_quote_versions[quote.id])
        self._missions.save(mission, expected_version=expected_mission_version)

        for quote in current_quotes:
            self._events.add_aggregate_events(quote)
        self._events.add_aggregate_events(mission)
        self._events.add_aggregate_events(booking)
        return booking

    def get_booking(self, booking_id: BookingId) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        return booking
