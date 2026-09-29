from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.aircraft import AircraftId
from charteros.domain.airports import AirportId
from charteros.domain.bookings import Booking, BookingId, BookingState
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.time_range import TimeRange

NOW = datetime(2025, 9, 29, 12, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _booking() -> Booking:
    return Booking.create(
        mission_id=MissionId(_id(1)),
        accepted_quote_id=QuoteId(_id(2)),
        operator_id=OperatorId(_id(3)),
        aircraft_id=AircraftId(_id(4)),
        created_at=NOW,
    )


def _selected_mission() -> Mission:
    return Mission(
        MissionId(_id(10)),
        buyer_id=OrganizationId(_id(11)),
        origin_airport_id=AirportId(_id(12)),
        destination_airport_id=AirportId(_id(13)),
        departure_window=TimeRange(NOW + timedelta(days=3), NOW + timedelta(days=3, hours=2)),
        passenger_count=20,
        max_budget=None,
        special_requirements=(),
        status=MissionStatus.SELECTED,
        version=5,
    )


def test_booking_happy_path_is_monotonic_versioned_and_evented() -> None:
    booking = _booking()
    transitions = [
        (booking.mark_contracted, BookingState.CONTRACTED, "BOOKING_CONTRACTED"),
        (
            booking.mark_payment_pending,
            BookingState.PAYMENT_PENDING,
            "BOOKING_PAYMENT_PENDING",
        ),
        (booking.confirm, BookingState.CONFIRMED, "BOOKING_CONFIRMED"),
        (
            booking.enter_pre_operation,
            BookingState.PRE_OPERATION,
            "BOOKING_PRE_OPERATION",
        ),
        (booking.start_operation, BookingState.OPERATING, "BOOKING_OPERATING"),
        (booking.complete, BookingState.COMPLETED, "BOOKING_COMPLETED"),
        (booking.reconcile, BookingState.RECONCILED, "BOOKING_RECONCILED"),
    ]

    for ordinal, (command, expected_state, expected_event) in enumerate(transitions, start=1):
        transition_time = NOW + timedelta(minutes=ordinal)
        command(transitioned_at=transition_time)
        assert booking.state is expected_state
        assert booking.state_changed_at == transition_time
        assert booking.pending_events[-1].event_type == expected_event
        assert booking.pending_events[-1].aggregate_version == ordinal + 1

    assert booking.version == 8
    assert [event.event_type for event in booking.pending_events] == [
        "BOOKING_CREATED",
        "BOOKING_CONTRACTED",
        "BOOKING_PAYMENT_PENDING",
        "BOOKING_CONFIRMED",
        "BOOKING_PRE_OPERATION",
        "BOOKING_OPERATING",
        "BOOKING_COMPLETED",
        "BOOKING_RECONCILED",
    ]


def test_booking_rejects_skipped_repeated_and_backdated_transitions() -> None:
    booking = _booking()

    with pytest.raises(DomainValidationError, match="payment_pending"):
        booking.confirm(transitioned_at=NOW + timedelta(minutes=1))

    booking.mark_contracted(transitioned_at=NOW + timedelta(minutes=2))
    with pytest.raises(DomainValidationError, match="contracted"):
        booking.mark_contracted(transitioned_at=NOW + timedelta(minutes=3))

    with pytest.raises(DomainValidationError, match="cannot precede"):
        booking.mark_payment_pending(transitioned_at=NOW + timedelta(minutes=1))


def test_booking_transition_requires_timezone_aware_timestamp() -> None:
    booking = _booking()

    with pytest.raises(DomainValidationError, match="timezone-aware"):
        booking.mark_contracted(transitioned_at=datetime(2025, 9, 29, 12, 1))


def test_booking_rehydration_rejects_state_timestamp_before_creation() -> None:
    with pytest.raises(DomainValidationError, match="cannot precede booking creation"):
        Booking(
            BookingId(_id(20)),
            mission_id=MissionId(_id(21)),
            accepted_quote_id=QuoteId(_id(22)),
            operator_id=OperatorId(_id(23)),
            aircraft_id=AircraftId(_id(24)),
            state=BookingState.CONTRACTED,
            created_at=NOW,
            state_changed_at=NOW - timedelta(seconds=1),
            version=2,
        )


def test_mission_booking_milestones_are_explicit_and_monotonic() -> None:
    mission = _selected_mission()

    mission.begin_contracting(transitioned_at=NOW + timedelta(minutes=1))
    mission.mark_booked(transitioned_at=NOW + timedelta(minutes=2))
    mission.start_operating(transitioned_at=NOW + timedelta(minutes=3))
    mission.complete(transitioned_at=NOW + timedelta(minutes=4))

    assert mission.status is MissionStatus.COMPLETED
    assert mission.version == 9
    assert [event.event_type for event in mission.pending_events] == [
        "MISSION_CONTRACTING",
        "MISSION_BOOKED",
        "MISSION_OPERATING",
        "MISSION_COMPLETED",
    ]

    with pytest.raises(DomainValidationError, match="operating"):
        mission.complete(transitioned_at=NOW + timedelta(minutes=5))
