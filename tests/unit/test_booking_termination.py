from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import (
    Booking,
    BookingState,
    BookingTerminationReason,
    BookingTerminationSource,
)
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.exceptions import DomainValidationError

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


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


@pytest.mark.parametrize(
    ("reason", "expected_state", "expected_source"),
    [
        (
            BookingTerminationReason.CONTRACT_UNSIGNED,
            BookingState.EXPIRED,
            BookingTerminationSource.SYSTEM,
        ),
        (
            BookingTerminationReason.COMMERCIAL_EXPIRY,
            BookingState.EXPIRED,
            BookingTerminationSource.SYSTEM,
        ),
        (
            BookingTerminationReason.BUYER_CANCEL,
            BookingState.CANCELLED,
            BookingTerminationSource.BUYER,
        ),
        (
            BookingTerminationReason.OPERATOR_RELEASE,
            BookingState.CANCELLED,
            BookingTerminationSource.OPERATOR,
        ),
    ],
)
def test_pending_contract_termination_is_reasoned_and_evented(
    reason: BookingTerminationReason,
    expected_state: BookingState,
    expected_source: BookingTerminationSource,
) -> None:
    booking = _booking()

    booking.terminate(reason=reason, transitioned_at=NOW + timedelta(minutes=1))

    assert booking.state is expected_state
    assert booking.termination_reason is reason
    assert booking.termination_source is expected_source
    assert booking.version == 2
    event = booking.pending_events[-1]
    assert event.event_type == (
        "BOOKING_EXPIRED" if expected_state is BookingState.EXPIRED else "BOOKING_CANCELLED"
    )
    assert event.payload["reason"] == reason.value
    assert event.payload["source"] == expected_source.value
    assert event.payload["from_state"] == "pending_contract"


def test_payment_pending_timeout_and_commercial_expiry_are_valid() -> None:
    for reason in (
        BookingTerminationReason.DEPOSIT_TIMEOUT,
        BookingTerminationReason.COMMERCIAL_EXPIRY,
    ):
        booking = _booking()
        booking.mark_contracted(transitioned_at=NOW + timedelta(minutes=1))
        booking.mark_payment_pending(transitioned_at=NOW + timedelta(minutes=2))

        booking.terminate(reason=reason, transitioned_at=NOW + timedelta(minutes=3))

        assert booking.state is BookingState.EXPIRED
        assert booking.termination_source is BookingTerminationSource.SYSTEM


def test_reason_state_matrix_and_terminal_reentry_fail_closed() -> None:
    booking = _booking()
    with pytest.raises(DomainValidationError, match="deposit_timeout"):
        booking.terminate(
            reason=BookingTerminationReason.DEPOSIT_TIMEOUT,
            transitioned_at=NOW + timedelta(minutes=1),
        )

    booking.mark_contracted(transitioned_at=NOW + timedelta(minutes=2))
    with pytest.raises(DomainValidationError, match="commercial_expiry"):
        booking.terminate(
            reason=BookingTerminationReason.COMMERCIAL_EXPIRY,
            transitioned_at=NOW + timedelta(minutes=3),
        )

    booking.terminate(
        reason=BookingTerminationReason.OPERATOR_RELEASE,
        transitioned_at=NOW + timedelta(minutes=4),
    )
    with pytest.raises(DomainValidationError, match="only pending_contract"):
        booking.terminate(
            reason=BookingTerminationReason.BUYER_CANCEL,
            transitioned_at=NOW + timedelta(minutes=5),
        )


def test_confirmed_booking_cannot_use_pr39_release_path() -> None:
    booking = _booking()
    booking.mark_contracted(transitioned_at=NOW + timedelta(minutes=1))
    booking.mark_payment_pending(transitioned_at=NOW + timedelta(minutes=2))
    booking.confirm(transitioned_at=NOW + timedelta(minutes=3))

    with pytest.raises(DomainValidationError, match="only pending_contract"):
        booking.terminate(
            reason=BookingTerminationReason.BUYER_CANCEL,
            transitioned_at=NOW + timedelta(minutes=4),
        )


def test_terminal_rehydration_requires_matching_reason_source_evidence() -> None:
    booking = _booking()

    with pytest.raises(DomainValidationError, match="termination evidence"):
        Booking(
            booking.id,
            mission_id=booking.mission_id,
            accepted_quote_id=booking.accepted_quote_id,
            operator_id=booking.operator_id,
            aircraft_id=booking.aircraft_id,
            state=BookingState.CANCELLED,
            created_at=NOW,
            state_changed_at=NOW + timedelta(minutes=1),
            version=2,
        )

    with pytest.raises(DomainValidationError, match="does not match"):
        Booking(
            booking.id,
            mission_id=booking.mission_id,
            accepted_quote_id=booking.accepted_quote_id,
            operator_id=booking.operator_id,
            aircraft_id=booking.aircraft_id,
            state=BookingState.CANCELLED,
            created_at=NOW,
            state_changed_at=NOW + timedelta(minutes=1),
            termination_reason=BookingTerminationReason.BUYER_CANCEL,
            termination_source=BookingTerminationSource.SYSTEM,
            version=2,
        )
