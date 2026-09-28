from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.shared.config import Settings


def settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def setup_matching_state(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, str, datetime, UUID, str]:
    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr6-origin-{suffix}"},
        json={
            "icao": f"Q{suffix}A",
            "iata": f"{suffix}O",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr6-destination-{suffix}"},
        json={
            "icao": f"Q{suffix}B",
            "iata": f"{suffix}D",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr6-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR6 Buyer {suffix}",
            "country": "GR",
        },
    )
    operator_org = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr6-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR6 Operator {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == operator_org.status_code == 201

    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr6-operator-{suffix}"},
        json={
            "organization_id": operator_org.json()["id"],
            "aoc_reference": f"GR-PR6-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201

    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr6-aircraft-{suffix}"},
        json={
            "operator_id": operator.json()["id"],
            "registration": f"SX-M{suffix}",
            "aircraft_type": {
                "manufacturer": "Airbus",
                "model": f"A320 PR6 {suffix}",
                "category": "airliner",
                "seats_min": 120,
                "seats_max": 190,
                "range_nm": 3300,
            },
            "seat_capacity": 180,
            "cargo_capacity": "1500",
            "range_nm": 3200,
            "home_base": origin.json()["id"],
        },
    )
    assert aircraft.status_code == 201

    departure = datetime.now(UTC) + timedelta(days=7)
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr6-mission-{suffix}"},
        json={
            "buyer_id": buyer.json()["id"],
            "origin_airport_id": origin.json()["id"],
            "destination_airport_id": destination.json()["id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 100,
            "max_budget": {"amount_minor": 20_000_000, "currency": "EUR"},
        },
    )
    assert mission.status_code == 201
    mission_id = mission.json()["id"]

    lifecycle_invalid = client.get(f"/v1/missions/{mission_id}/matches")
    assert lifecycle_invalid.status_code == 409

    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr6-open-{suffix}"},
    )
    assert opened.status_code == 200

    position = client.post(
        f"/v1/aircraft/{aircraft.json()['id']}/positions",
        headers={"Idempotency-Key": f"pr6-position-{suffix}"},
        json={
            "airport_id": origin.json()["id"],
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr6-integration",
            "provenance": {"fixture": suffix},
        },
    )
    assert position.status_code == 201

    availability = client.post(
        f"/v1/aircraft/{aircraft.json()['id']}/availability",
        headers={"Idempotency-Key": f"pr6-availability-{suffix}"},
        json={
            "valid_from": (departure - timedelta(hours=1)).isoformat(),
            "valid_to": (departure + timedelta(hours=3)).isoformat(),
            "status": "available",
            "source": "pr6-integration",
            "provenance": {"fixture": suffix},
        },
    )
    assert availability.status_code == 201

    return (
        mission_id,
        aircraft.json()["id"],
        departure,
        UUID(aircraft.json()["aircraft_type_id"]),
        availability.json()["id"],
    )


def insert_profile(
    settings_value: Settings,
    aircraft_type_id: UUID,
    *,
    recorded_at: datetime,
) -> None:
    engine = create_engine(settings_value.database_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                MatchingReferenceProfileRow(
                    id=uuid4(),
                    aircraft_type_id=aircraft_type_id,
                    cruise_speed_kts=450,
                    operating_cost_per_hour_minor=600_000,
                    operating_cost_currency="EUR",
                    max_reposition_nm=1_000,
                    turnaround_buffer_minutes=45,
                    source="validated-test-reference",
                    provenance={"dataset": "pr6-integration", "revision": 1},
                    recorded_at=recorded_at,
                    superseded_at=None,
                )
            )
    finally:
        engine.dispose()
