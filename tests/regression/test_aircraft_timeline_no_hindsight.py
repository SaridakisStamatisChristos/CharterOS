import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from charteros.domain.aircraft import (
    AircraftId,
    AircraftPositionObservation,
    PositionObservationId,
    select_position_as_of,
)
from charteros.domain.airports import AirportId

FIXTURE = Path("test-assets/charter-graph/bitemporal_no_hindsight_v0.1.0.json")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.regression
def test_charter_graph_reference_scenario_has_no_hindsight_leakage() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    aircraft_id = AircraftId(UUID(payload["aircraft_id"]))
    observations = tuple(
        AircraftPositionObservation(
            id=PositionObservationId(UUID(item["id"])),
            aircraft_id=aircraft_id,
            airport_id=AirportId(UUID(item["airport_id"])),
            latitude=None,
            longitude=None,
            event_time=_dt(item["event_time"]),
            recorded_at=_dt(item["recorded_at"]),
            source=item["source"],
            provenance={"fixture": "charter_graph_testbench_v0.1.0"},
            created_at=_dt(item["recorded_at"]),
        )
        for item in payload["positions"]
    )

    historical = select_position_as_of(
        observations,
        event_time=_dt(payload["decision_event_time"]),
        known_as_of=_dt(payload["historical_known_as_of"]),
    )
    later = select_position_as_of(
        observations,
        event_time=_dt(payload["decision_event_time"]),
        known_as_of=_dt(payload["later_known_as_of"]),
    )

    assert historical is not None
    assert later is not None
    assert str(historical.airport_id) == payload["expected_historical_airport_id"]
    assert str(later.airport_id) == payload["expected_later_airport_id"]


@pytest.mark.regression
def test_exact_knowledge_boundary_is_inclusive() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    item = payload["positions"][1]
    observation = AircraftPositionObservation(
        id=PositionObservationId(UUID(item["id"])),
        aircraft_id=AircraftId(UUID(payload["aircraft_id"])),
        airport_id=AirportId(UUID(item["airport_id"])),
        latitude=None,
        longitude=None,
        event_time=_dt(item["event_time"]),
        recorded_at=_dt(item["recorded_at"]),
        source=item["source"],
        provenance={},
        created_at=_dt(item["recorded_at"]),
    )
    assert (
        select_position_as_of(
            (observation,),
            event_time=_dt(payload["decision_event_time"]),
            known_as_of=observation.recorded_at,
        )
        == observation
    )
