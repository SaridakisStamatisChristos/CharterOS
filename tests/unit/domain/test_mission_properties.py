from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.time_range import TimeRange


@pytest.mark.property
@given(
    passenger_count=st.integers(min_value=1, max_value=100_000),
    duration_minutes=st.integers(min_value=1, max_value=60 * 24 * 30),
)
def test_valid_mission_inputs_preserve_passenger_count_and_half_open_window(
    passenger_count: int, duration_minutes: int
) -> None:
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    end = start + timedelta(minutes=duration_minutes)
    mission = Mission.create(
        buyer_id=OrganizationId(uuid4()),
        origin_airport_id=AirportId(uuid4()),
        destination_airport_id=AirportId(uuid4()),
        departure_window=TimeRange(start, end),
        passenger_count=passenger_count,
        recorded_at=start,
    )

    assert mission.passenger_count == passenger_count
    assert mission.departure_window.contains(start)
    assert not mission.departure_window.contains(end)
