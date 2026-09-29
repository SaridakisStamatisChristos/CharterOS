import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _operator_aircraft(
    client: TestClient,
    *,
    suffix: str,
    ordinal: int,
    home_base: str,
) -> tuple[str, str]:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr17-op-org-{suffix}-{ordinal}"},
        json={
            "type": "operator",
            "legal_name": f"PR17 Operator {suffix} {ordinal}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr17-op-{suffix}-{ordinal}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR17-{suffix}-{ordinal}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    operator_id = str(operator.json()["id"])
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr17-aircraft-{suffix}-{ordinal}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-T{suffix}{ordinal}",
            "aircraft_type": {
                "manufacturer": "PR17 Airframes",
                "model": f"TenderJet {suffix}-{ordinal}",
                "category": "regional",
                "seats_min": 1,
                "seats_max": 80,
                "range_nm": 3000,
                "runway_requirements": {},
                "baggage_cargo_profile": {},
            },
            "seat_capacity": 60,
            "cargo_capacity": "800",
            "range_nm": 2500,
            "home_base": home_base,
            "status": "active",
        },
    )
    assert aircraft.status_code == 201
    return operator_id, str(aircraft.json()["id"])


def _quote_body(aircraft_id: str, valid_until: datetime, amount_minor: int) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount_minor,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 100_000,
            }
        ],
        "inclusions": ["Standard catering"],
        "valid_until": valid_until.isoformat(),
    }


def _setup_tender(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, str, datetime, list[dict[str, str]]]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr17-buyer-{suffix}"},
        json={"type": "buyer", "legal_name": f"PR17 Buyer {suffix}", "country": "GR"},
    )
    assert buyer.status_code == 201
    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr17-origin-{suffix}"},
        json={
            "icao": f"T{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr17-destination-{suffix}"},
        json={
            "icao": f"T{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    departure = datetime.now(UTC) + timedelta(days=7)
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr17-mission-{suffix}"},
        json={
            "buyer_id": buyer.json()["id"],
            "origin_airport_id": origin.json()["id"],
            "destination_airport_id": destination.json()["id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 20,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr17-open-mission-{suffix}"},
    )
    assert opened.status_code == 200

    tender = client.post(
        f"/v1/missions/{mission_id}/tenders",
        headers={"Idempotency-Key": f"pr17-tender-{suffix}"},
        json={
            "opens_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "deadline_at": (departure - timedelta(days=2)).isoformat(),
            "sealed_bid": True,
        },
    )
    assert tender.status_code == 201
    tender_id = str(tender.json()["id"])
    assert tender.json()["status"] == "open"

    suppliers: list[dict[str, str]] = []
    for ordinal, amount in ((1, 7_000_000), (2, 8_000_000)):
        operator_id, aircraft_id = _operator_aircraft(
