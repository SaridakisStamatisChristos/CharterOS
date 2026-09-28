from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from charteros.domain.aircraft import (
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AvailabilityStatus,
    select_position_as_of,
)
from charteros.domain.airports import AirportId

T0 = datetime(2026, 9, 28, tzinfo=UTC)
AIRCRAFT_ID = AircraftId(UUID(int=100))
AIRPORT_ID = AirportId(UUID(int=101))


@pytest.mark.property
@given(
    start_minutes=st.integers(min_value=-100_000, max_value=100_000),
    duration_minutes=st.integers(min_value=1, max_value=10_000),
)
def test_half_open_interval_boundaries_hold_for_generated_intervals(
    start_minutes: int, duration_minutes: int
) -> None:
    start = T0 + timedelta(minutes=start_minutes)
    end = start + timedelta(minutes=duration_minutes)
    record = AircraftAvailabilityRecord.create(
        aircraft_id=AIRCRAFT_ID,
        valid_from=start,
        valid_to=end,
        status=AvailabilityStatus.AVAILABLE,
        recorded_at=T0,
        source="property-test",
    )
    assert record.contains(start)
    assert not record.contains(end)


@pytest.mark.property
@given(
    event_minutes=st.integers(min_value=-10_000, max_value=0),
    delay_minutes=st.integers(min_value=0, max_value=10_000),
)
def test_position_visibility_never_crosses_knowledge_cutoff(
    event_minutes: int, delay_minutes: int
) -> None:
    event_time = T0 + timedelta(minutes=event_minutes)
    recorded_at = event_time + timedelta(minutes=delay_minutes)
    observation = AircraftPositionObservation.create(
        aircraft_id=AIRCRAFT_ID,
        airport_id=AIRPORT_ID,
        latitude=None,
        longitude=None,
        event_time=event_time,
        recorded_at=recorded_at,
        source="property-test",
    )

    before = recorded_at - timedelta(microseconds=1)
    assert (
        select_position_as_of(
            (observation,),
            event_time=event_time,
            known_as_of=before,
        )
        is None
    )
    assert (
        select_position_as_of(
            (observation,),
            event_time=event_time,
            known_as_of=recorded_at,
        )
        == observation
    )
