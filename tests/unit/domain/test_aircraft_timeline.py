from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from charteros.domain.aircraft import (
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AvailabilityRecordId,
    AvailabilityStatus,
    PositionObservationId,
    select_availability_as_of,
    select_position_as_of,
)
from charteros.domain.airports import AirportId
from charteros.domain.shared.exceptions import DomainValidationError

T0 = datetime(2026, 9, 28, 10, tzinfo=UTC)
AIRCRAFT_ID = AircraftId(UUID(int=1))
ATH_ID = AirportId(UUID(int=2))
FCO_ID = AirportId(UUID(int=3))


def test_position_requires_exactly_one_location_mode_and_aware_time() -> None:
    with pytest.raises(DomainValidationError):
        AircraftPositionObservation.create(
            aircraft_id=AIRCRAFT_ID,
            airport_id=None,
            latitude=None,
            longitude=None,
            event_time=T0,
            recorded_at=T0,
            source="ops",
        )

    with pytest.raises(DomainValidationError):
        AircraftPositionObservation.create(
            aircraft_id=AIRCRAFT_ID,
            airport_id=ATH_ID,
            latitude=Decimal("37.93"),
            longitude=Decimal("23.94"),
            event_time=T0,
            recorded_at=T0,
            source="ops",
        )

    with pytest.raises(DomainValidationError):
        AircraftPositionObservation.create(
            aircraft_id=AIRCRAFT_ID,
            airport_id=ATH_ID,
            latitude=None,
            longitude=None,
            event_time=datetime(2026, 9, 28, 10),
            recorded_at=T0,
            source="ops",
        )


def test_position_normalizes_aware_time_to_utc_and_rejects_future_knowledge() -> None:
    local = datetime(2026, 9, 28, 12, tzinfo=timezone(timedelta(hours=2)))
    observation = AircraftPositionObservation.create(
        aircraft_id=AIRCRAFT_ID,
        airport_id=ATH_ID,
        latitude=None,
        longitude=None,
        event_time=local,
        recorded_at=T0 + timedelta(minutes=15),
        source="  dispatch feed  ",
    )
    assert observation.event_time == T0
    assert observation.source == "dispatch feed"

    with pytest.raises(DomainValidationError):
        AircraftPositionObservation.create(
            aircraft_id=AIRCRAFT_ID,
            airport_id=ATH_ID,
            latitude=None,
            longitude=None,
            event_time=T0 + timedelta(minutes=1),
            recorded_at=T0,
            source="ops",
        )


def test_availability_is_half_open_and_rejects_invalid_interval() -> None:
    record = AircraftAvailabilityRecord.create(
        aircraft_id=AIRCRAFT_ID,
        valid_from=T0,
        valid_to=T0 + timedelta(hours=2),
        status=AvailabilityStatus.AVAILABLE,
        recorded_at=T0 - timedelta(hours=1),
        source="operator",
    )
    assert record.contains(T0)
    assert record.contains(T0 + timedelta(hours=1, minutes=59))
    assert not record.contains(T0 + timedelta(hours=2))

    with pytest.raises(DomainValidationError):
        AircraftAvailabilityRecord.create(
            aircraft_id=AIRCRAFT_ID,
            valid_from=T0,
            valid_to=T0,
            status=AvailabilityStatus.AVAILABLE,
            recorded_at=T0,
            source="operator",
        )


def test_position_selection_uses_event_time_then_knowledge_time_deterministically() -> None:
    old = AircraftPositionObservation(
        id=PositionObservationId(UUID(int=10)),
        aircraft_id=AIRCRAFT_ID,
        airport_id=ATH_ID,
        latitude=None,
        longitude=None,
        event_time=T0,
        recorded_at=T0 + timedelta(minutes=1),
        source="feed",
        provenance={},
        created_at=T0 + timedelta(minutes=1),
    )
    correction = AircraftPositionObservation(
        id=PositionObservationId(UUID(int=11)),
        aircraft_id=AIRCRAFT_ID,
        airport_id=FCO_ID,
        latitude=None,
        longitude=None,
        event_time=T0,
        recorded_at=T0 + timedelta(minutes=5),
        source="feed-correction",
        provenance={},
        created_at=T0 + timedelta(minutes=5),
    )

    assert (
        select_position_as_of(
            (correction, old),
            event_time=T0,
            known_as_of=T0 + timedelta(minutes=3),
        )
        == old
    )
    assert (
        select_position_as_of(
            (old, correction),
            event_time=T0,
            known_as_of=T0 + timedelta(minutes=5),
        )
        == correction
    )


def test_availability_correction_preserves_historical_authority() -> None:
    old = AircraftAvailabilityRecord(
        id=AvailabilityRecordId(UUID(int=20)),
        aircraft_id=AIRCRAFT_ID,
        interval=AircraftAvailabilityRecord.create(
            aircraft_id=AIRCRAFT_ID,
            valid_from=T0,
            valid_to=T0 + timedelta(hours=4),
            status=AvailabilityStatus.AVAILABLE,
            recorded_at=T0 - timedelta(hours=1),
            source="operator",
        ).interval,
        status=AvailabilityStatus.AVAILABLE,
        recorded_at=T0 - timedelta(hours=1),
        source="operator",
        reason=None,
        provenance={},
        supersedes_id=None,
        superseded_at=None,
        created_at=T0 - timedelta(hours=1),
    )
    correction_time = T0 + timedelta(minutes=30)
    correction = AircraftAvailabilityRecord(
        id=AvailabilityRecordId(UUID(int=21)),
        aircraft_id=AIRCRAFT_ID,
        interval=old.interval,
        status=AvailabilityStatus.RESERVED,
        recorded_at=correction_time,
        source="operator-correction",
        reason="late reservation sync",
        provenance={},
        supersedes_id=old.id,
        superseded_at=None,
        created_at=correction_time,
    )
    old_closed = replace(old, superseded_at=correction_time)

    assert (
        select_availability_as_of(
            (old_closed, correction),
            event_time=T0 + timedelta(hours=1),
            known_as_of=correction_time - timedelta(microseconds=1),
        )
        == old_closed
    )
    assert (
        select_availability_as_of(
            (old_closed, correction),
            event_time=T0 + timedelta(hours=1),
            known_as_of=correction_time,
        )
        == correction
    )
