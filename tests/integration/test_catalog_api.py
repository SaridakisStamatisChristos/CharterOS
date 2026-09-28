import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings


@pytest.mark.integration
def test_catalog_creation_flow_constraints_and_outbox() -> None:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")

    settings = Settings(
        environment="test",
        database_url=database_url,
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        missing_key = client.post(
            "/v1/organizations",
            json={
                "type": "operator",
                "legal_name": "Missing Key Operator",
                "country": "GR",
            },
        )
        assert missing_key.status_code == 422

        airport_response = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "airport-create-1"},
            json={
                "icao": "LGAV",
                "iata": "ATH",
                "lat": "37.9364",
                "lon": "23.9445",
                "timezone": "Europe/Athens",
                "runway_metadata": {"longest_runway_m": 4000},
                "curfew_metadata": {},
                "operational_flags": ["international"],
            },
        )
        assert airport_response.status_code == 201
        airport = airport_response.json()
        UUID(airport["id"])

        organization_response = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "org-create-1"},
            json={
                "type": "operator",
                "legal_name": "Aegean Charter Test",
                "trading_name": "Aegean Test",
                "country": "GR",
            },
        )
        assert organization_response.status_code == 201
        organization = organization_response.json()

        replay = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "org-create-1"},
            json={
                "type": "operator",
                "legal_name": "Aegean Charter Test",
                "trading_name": "Aegean Test",
                "country": "GR",
            },
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == organization["id"]

        key_reuse_conflict = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "org-create-1"},
            json={
                "type": "operator",
                "legal_name": "Different Operator",
                "country": "GR",
            },
        )
        assert key_reuse_conflict.status_code == 409

        duplicate = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "org-create-duplicate"},
            json={
                "type": "operator",
                "legal_name": "  aegean charter test ",
                "country": "GR",
            },
        )
        assert duplicate.status_code == 409

        operator_response = client.post(
            "/v1/operators",
            headers={"Idempotency-Key": "operator-create-1"},
            json={
                "organization_id": organization["id"],
                "aoc_reference": "GR-TEST-001",
                "operating_regions": ["EU", "MED"],
            },
        )
        assert operator_response.status_code == 201
        operator = operator_response.json()

        aircraft_response = client.post(
            "/v1/aircraft",
            headers={"Idempotency-Key": "aircraft-create-1"},
            json={
                "operator_id": operator["id"],
                "registration": "SX-TST",
                "aircraft_type": {
                    "manufacturer": "Airbus",
                    "model": "A320-200 TEST",
                    "category": "airliner",
                    "seats_min": 150,
                    "seats_max": 186,
                    "range_nm": 3300,
                    "runway_requirements": {},
                    "baggage_cargo_profile": {},
                },
                "seat_capacity": 180,
                "cargo_capacity": "1500",
                "range_nm": 3200,
                "home_base": airport["id"],
            },
        )
        assert aircraft_response.status_code == 201
        aircraft = aircraft_response.json()
        assert aircraft["registration"] == "SX-TST"
        UUID(aircraft["aircraft_type_id"])

        duplicate_aircraft = client.post(
            "/v1/aircraft",
            headers={"Idempotency-Key": "aircraft-create-duplicate"},
            json={
                "operator_id": operator["id"],
                "registration": "sx-tst",
                "aircraft_type": {
                    "manufacturer": "Airbus",
                    "model": "A320-200 TEST",
                    "category": "airliner",
                    "seats_min": 150,
                    "seats_max": 186,
                    "range_nm": 3300,
                },
                "seat_capacity": 180,
                "cargo_capacity": "1500",
                "range_nm": 3200,
                "home_base": airport["id"],
            },
        )
        assert duplicate_aircraft.status_code == 409

        invalid_timezone = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "airport-invalid-timezone"},
            json={
                "icao": "TEST",
                "lat": "10",
                "lon": "20",
                "timezone": "Mars/Olympus",
            },
        )
        assert invalid_timezone.status_code == 422

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            outbox_count = connection.execute(
                text("SELECT count(*) FROM outbox_events")
            ).scalar_one()
            idempotency_count = connection.execute(
                text("SELECT count(*) FROM idempotency_records")
            ).scalar_one()
            assert outbox_count == 4
            assert idempotency_count == 4
    finally:
        engine.dispose()
