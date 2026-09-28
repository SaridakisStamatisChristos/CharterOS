from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.aircraft import AircraftId
from charteros.domain.airports import AirportId
from charteros.domain.bookings import Booking, BookingState
from charteros.domain.missions import Mission, MissionStatus
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import Quote, QuoteStatus
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange

NOW = datetime(2026, 9, 29, 8, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _quote(*, rfq: int = 1, aircraft: int = 2) -> Quote:
    return Quote.submit(
        rfq_id=RfqId(_id(rfq)),
        aircraft_id=AircraftId(_id(aircraft)),
        base_price=Money(8_200_000, Currency("EUR")),
        price_components=(),
        repositioning_cost=None,
        inclusions=(),
        exclusions=(),
        cancellation_terms=None,
        payment_terms=None,
        valid_until=NOW + timedelta(days=1),
        submitted_at=NOW,
    )


def test_booking_creation_is_minimal_and_emits_creation_event() -> None:
    booking = Booking.create(
        mission_id=Mission.create(
            buyer_id=OrganizationId(_id(10)),
            origin_airport_id=AirportId(_id(11)),
            destination_airport_id=AirportId(_id(12)),
            departure_window=TimeRange(
                NOW + timedelta(days=2),
                NOW + timedelta(days=2, hours=2),
            ),
            passenger_count=20,
        ).id,
        accepted_quote_id=_quote().id,
        operator_id=OperatorId(_id(13)),
        aircraft_id=AircraftId(_id(14)),
        created_at=NOW,
    )

    assert booking.state is BookingState.PENDING_CONTRACT
    assert booking.version == 1
    assert booking.pending_events[-1].event_type == "BOOKING_CREATED"
    assert booking.pending_events[-1].payload["accepted_quote_id"] == str(
        booking.accepted_quote_id
    )


def test_quote_accept_and_losing_quote_reject_are_explicit_terminal_states() -> None:
    winner = _quote(rfq=20, aircraft=21)
    loser = _quote(rfq=22, aircraft=23)

    winner.accept(
        accepted_at=NOW + timedelta(minutes=5),
        booking_id=str(_id(24)),
    )
    loser.reject(
        rejected_at=NOW + timedelta(minutes=5),
        accepted_quote_id=str(winner.id),
    )

    assert winner.status is QuoteStatus.ACCEPTED
    assert winner.is_current is False
    assert winner.accepted_at == NOW + timedelta(minutes=5)
    assert winner.pending_events[-1].event_type == "QUOTE_ACCEPTED"

    assert loser.status is QuoteStatus.REJECTED
    assert loser.is_current is False
    assert loser.rejected_at == NOW + timedelta(minutes=5)
    assert loser.pending_events[-1].event_type == "QUOTE_REJECTED"

    with pytest.raises(DomainValidationError, match="current submitted"):
        winner.accept(
            accepted_at=NOW + timedelta(minutes=6),
            booking_id=str(_id(25)),
        )


def test_quote_cannot_be_accepted_at_or_after_valid_until() -> None:
    quote = _quote(rfq=30, aircraft=31)

    with pytest.raises(DomainValidationError, match="validity window"):
        quote.accept(
            accepted_at=quote.valid_until,
            booking_id=str(_id(32)),
        )


def test_mission_selection_preserves_explicit_quoted_then_selected_transitions() -> None:
    mission = Mission.create(
        buyer_id=OrganizationId(_id(40)),
        origin_airport_id=AirportId(_id(41)),
        destination_airport_id=AirportId(_id(42)),
        departure_window=TimeRange(
            NOW + timedelta(days=2),
            NOW + timedelta(days=2, hours=2),
        ),
        passenger_count=20,
    )
    mission.open()
    mission.start_sourcing()
    mission.select_quote(
        quote_id=str(_id(43)),
        booking_id=str(_id(44)),
        selected_at=NOW + timedelta(minutes=10),
    )

    assert mission.status is MissionStatus.SELECTED
    assert mission.version == 5
    assert [event.event_type for event in mission.pending_events] == [
        "MISSION_CREATED",
        "MISSION_OPENED",
        "MISSION_SOURCING",
        "MISSION_QUOTED",
        "MISSION_SELECTED",
    ]
