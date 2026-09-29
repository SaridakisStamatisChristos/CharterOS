import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from charteros.shared.config import Settings


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _organization(client: TestClient, *, suffix: str, kind: str) -> str:
    response = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr21-org-{suffix}"},
        json={
            "type": kind,
            "legal_name": f"PR21 {kind.title()} {suffix}",
            "country": "GR",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _operator(client: TestClient, *, suffix: str) -> str:
    organization_id = _organization(client, suffix=f"OP-{suffix}", kind="operator")
    response = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr21-operator-{suffix}"},
        json={
            "organization_id": organization_id,
            "aoc_reference": f"GR-PR21-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _airport(client: TestClient, *, suffix: str, ordinal: int) -> str:
    icao = f"P{ordinal}{suffix}"[:4]
    iata = f"{ordinal}{suffix}"[:3]
    response = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr21-airport-{suffix}-{ordinal}"},
        json={
            "icao": icao,
            "iata": iata,
            "lat": str(37 + ordinal / 10),
            "lon": str(23 + ordinal / 10),
            "timezone": "Europe/Athens",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _portal_aircraft(
    client: TestClient,
    *,
    operator_id: str,
    home_base: str,
    suffix: str,
) -> str:
    body = {
        "registration": f"SX-{suffix}",
        "aircraft_type": {
            "manufacturer": "PR21 Airframes",
            "model": f"PortalJet {suffix}",
            "category": "regional",
            "seats_min": 1,
            "seats_max": 80,
            "range_nm": 3000,
            "runway_requirements": {},
            "baggage_cargo_profile": {},
        },
        "seat_capacity": 60,
        "cargo_capacity": "700",
        "range_nm": 2500,
        "home_base": home_base,
        "status": "active",
    }
    response = client.post(
        "/v1/operator-portal/fleet",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr21-fleet-{suffix}",
        },
        json=body,
    )
    assert response.status_code == 201
    replay = client.post(
        "/v1/operator-portal/fleet",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr21-fleet-{suffix}",
        },
        json=body,
    )
    assert replay.status_code == 201
    assert replay.json() == response.json()
    return str(response.json()["id"])


def _mission_and_rfq(
    client: TestClient,
    *,
    suffix: str,
    buyer_id: str,
    operator_id: str,
    origin_id: str,
    destination_id: str,
    departure: datetime,
) -> tuple[str, str]:
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr21-mission-{suffix}"},
        json={
            "buyer_id": buyer_id,
            "origin_airport_id": origin_id,
            "destination_airport_id": destination_id,
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 18,
            "special_requirements": ["wifi"],
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr21-open-{suffix}"},
    )
    assert opened.status_code == 200
    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr21-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    return mission_id, str(rfq.json()["id"])


def _quote_body(aircraft_id: str, valid_until: datetime, amount: int) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount,
        "repositioning_amount_minor": 100_000,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 50_000,
            }
        ],
        "inclusions": ["catering"],
        "exclusions": [],
        "valid_until": valid_until.isoformat(),
    }


@pytest.mark.integration
def test_pr21_operator_portal_isolation_workflows_and_calendar() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id = _organization(client, suffix="BUY-A", kind="buyer")
        origin_id = _airport(client, suffix="A1", ordinal=1)
        destination_id = _airport(client, suffix="A2", ordinal=2)
        operator_a = _operator(client, suffix="A")
        operator_b = _operator(client, suffix="B")
        aircraft_a = _portal_aircraft(
            client,
            operator_id=operator_a,
            home_base=origin_id,
            suffix="P21A",
        )
        aircraft_b = _portal_aircraft(
            client,
            operator_id=operator_b,
            home_base=origin_id,
            suffix="P21B",
        )

        fleet_a = client.get(
            "/v1/operator-portal/fleet?limit=1",
            headers={"X-Operator-Id": operator_a},
        )
        assert fleet_a.status_code == 200
        assert fleet_a.json()["returned_count"] == 1
        assert {item["operator_id"] for item in fleet_a.json()["aircraft"]} == {operator_a}

        cross_fleet = client.get(
            f"/v1/operator-portal/fleet/{aircraft_b}",
            headers={"X-Operator-Id": operator_a},
        )
        assert cross_fleet.status_code == 404

        availability_body = {
            "valid_from": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "valid_to": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "status": "available",
            "source": "operator-portal",
            "reason": "planned availability",
            "provenance": {"source": "pr21-test"},
        }
        forbidden_availability = client.post(
            f"/v1/operator-portal/fleet/{aircraft_b}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-cross-availability-a",
            },
            json=availability_body,
        )
        assert forbidden_availability.status_code == 404

        availability = client.post(
            f"/v1/operator-portal/fleet/{aircraft_a}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-availability-a",
            },
            json=availability_body,
        )
        assert availability.status_code == 201
        replay = client.post(
            f"/v1/operator-portal/fleet/{aircraft_a}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-availability-a",
            },
            json=availability_body,
        )
        assert replay.status_code == 201
        assert replay.json() == availability.json()

        departure = datetime.now(UTC) + timedelta(days=10)
        mission_id, rfq_id = _mission_and_rfq(
            client,
            suffix="GEN-A",
            buyer_id=buyer_id,
            operator_id=operator_a,
            origin_id=origin_id,
            destination_id=destination_id,
            departure=departure,
        )
