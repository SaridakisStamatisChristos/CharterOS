from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission, MissionStatus
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange


def _mission() -> Mission:
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    return Mission.create(
        buyer_id=OrganizationId(uuid4()),
        origin_airport_id=AirportId(uuid4()),
        destination_airport_id=AirportId(uuid4()),
        departure_window=TimeRange(start, start + timedelta(hours=2)),
        passenger_count=42,
        max_budget=Money(7_500_000, Currency("EUR")),
        special_requirements=(" Wheelchair assistance ", "wheelchair assistance", "Catering"),
        recorded_at=start,
    )


def test_mission_creation_is_canonical_and_emits_event() -> None:
    mission = _mission()

    assert mission.status is MissionStatus.DRAFT
    assert mission.version == 1
    assert mission.special_requirements == ("Wheelchair assistance", "Catering")
    assert mission.pending_events[0].event_type == "MISSION_CREATED"


def test_mission_open_is_explicit_and_monotonic() -> None:
    mission = _mission()
    mission.collect_events()
    command_time = datetime(2026, 10, 1, 10, 1, tzinfo=UTC)

    mission.open(recorded_at=command_time)

    assert mission.status is MissionStatus.OPEN
    assert mission.version == 2
    assert mission.pending_events[0].event_type == "MISSION_OPENED"
    with pytest.raises(DomainValidationError):
        mission.open(recorded_at=command_time)


def test_mission_rejects_invalid_route_passengers_and_budget() -> None:
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    airport = AirportId(uuid4())
    buyer_id = OrganizationId(uuid4())
    window = TimeRange(start, start + timedelta(hours=2))
    with pytest.raises(DomainValidationError):
        Mission.create(
            buyer_id=buyer_id,
            origin_airport_id=airport,
            destination_airport_id=airport,
            departure_window=window,
            passenger_count=1,
            recorded_at=start,
        )

    with pytest.raises(DomainValidationError):
        Mission.create(
            buyer_id=buyer_id,
            origin_airport_id=airport,
            destination_airport_id=AirportId(uuid4()),
            departure_window=window,
            passenger_count=0,
            recorded_at=start,
        )

    with pytest.raises(DomainValidationError):
        Mission.create(
            buyer_id=buyer_id,
            origin_airport_id=airport,
            destination_airport_id=AirportId(uuid4()),
            departure_window=window,
            passenger_count=1,
            max_budget=Money(0, Currency("EUR")),
            recorded_at=start,
        )
