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
    return Settings(
        environment="test",
        database_url=database_url,
        _env_file=None,
    )


def _create_aircraft(
    client: TestClient, *, suffix: str, icao: str, iata: str
) -> tuple[str, str]:
    airport = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr4-airport-{suffix}"},
        json={
            "icao": icao,
            "iata": iata,
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    assert airport.status_code == 201
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr4-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR4 Operator {suffix}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr4-operator-{suffix}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR4-{suffix}",
            "operating_regions": ["EU"],
        },
    )
    assert operator.status_code == 201
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr4-aircraft-{suffix}"},
        json={
            "operator_id": operator.json()["id"],
            "registration": f"SX-P{suffix}",
            "aircraft_type": {
                "manufacturer": "Airbus",
                "model": f"A320 PR4 {suffix}",
                "category": "airliner",
                "seats_min": 150,
                "seats_max": 186,
                "range_nm": 3300,
            },
            "seat_capacity": 180,
            "cargo_capacity": "1500",
            "range_nm": 3200,
            "home_base": airport.json()["id"],
        },
    )
    assert aircraft.status_code == 201
    return aircraft.json()["id"], airport.json()["id"]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.integration
def test_fleet_timeline_api_idempotency_history_correction_and_outbox() -> None:
    settings = _settings()
    now = datetime.now(UTC)

    with TestClient(create_app(settings)) as client:
        aircraft_id, airport_id = _create_aircraft(client, suffix="A", icao="PRTA", iata="PRA")
        UUID(aircraft_id)

        position_body = {
            "airport_id": airport_id,
            "event_time": (now - timedelta(hours=2)).isoformat(),
            "source": "dispatch-feed",
            "provenance": {"message_id": "pos-a"},
        }
        position_response = client.post(
            f"/v1/aircraft/{aircraft_id}/positions",
            headers={"Idempotency-Key": "pr4-position-a"},
            json=position_body,
        )
        assert position_response.status_code == 201
        position = position_response.json()
        assert position["aircraft_version"] == 2

        replay = client.post(
            f"/v1/aircraft/{aircraft_id}/positions",
            headers={"Idempotency-Key": "pr4-position-a"},
            json=position_body,
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == position["id"]

        key_conflict_body = dict(position_body)
        key_conflict_body["source"] = "different-source"
        key_conflict = client.post(
            f"/v1/aircraft/{aircraft_id}/positions",
            headers={"Idempotency-Key": "pr4-position-a"},
            json=key_conflict_body,
        )
        assert key_conflict.status_code == 409

        recorded_at = _parse(position["recorded_at"])
        event_time = _parse(position["event_time"])
        window_from = now - timedelta(hours=3)
        window_to = now + timedelta(hours=8)
        before_knowledge = recorded_at - timedelta(microseconds=1)

        before = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "known_as_of": before_knowledge.isoformat(),
                "at": (event_time + timedelta(hours=1)).isoformat(),
            },
        )
        assert before.status_code == 200
        assert before.json()["positions"] == []
        assert before.json()["state"]["position"] is None

        exact = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "known_as_of": recorded_at.isoformat(),
                "at": (event_time + timedelta(hours=1)).isoformat(),
            },
        )
        assert exact.status_code == 200
        assert exact.json()["state"]["position"]["id"] == position["id"]

        availability_body = {
            "valid_from": (now + timedelta(hours=2)).isoformat(),
            "valid_to": (now + timedelta(hours=6)).isoformat(),
            "status": "available",
            "source": "operator-calendar",
            "reason": "published duty window",
            "provenance": {"calendar_revision": 1},
        }
        availability_response = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr4-availability-a"},
            json=availability_body,
        )
        assert availability_response.status_code == 201
        availability = availability_response.json()
        assert availability["aircraft_version"] == 3

        availability_recorded_at = _parse(availability["recorded_at"])
        before_availability = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "known_as_of": (availability_recorded_at - timedelta(microseconds=1)).isoformat(),
                "at": (now + timedelta(hours=3)).isoformat(),
            },
        )
        assert before_availability.status_code == 200
        assert before_availability.json()["state"]["availability"] is None

        overlap = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr4-availability-overlap"},
            json={
                **availability_body,
                "valid_from": (now + timedelta(hours=3)).isoformat(),
                "valid_to": (now + timedelta(hours=5)).isoformat(),
            },
        )
        assert overlap.status_code == 409

        invalid_interval = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr4-availability-invalid"},
            json={
                **availability_body,
                "valid_from": (now + timedelta(hours=6)).isoformat(),
                "valid_to": (now + timedelta(hours=6)).isoformat(),
            },
        )
        assert invalid_interval.status_code == 422

        correction_response = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr4-availability-correction"},
            json={
                **availability_body,
                "status": "reserved",
                "reason": "late reservation sync",
                "provenance": {"calendar_revision": 2},
                "supersedes_id": availability["id"],
            },
        )
        assert correction_response.status_code == 201
        correction = correction_response.json()
        assert correction["aircraft_version"] == 4

        historical = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "known_as_of": availability_recorded_at.isoformat(),
                "at": (now + timedelta(hours=3)).isoformat(),
            },
        )
        assert historical.status_code == 200
        assert historical.json()["state"]["availability"]["id"] == availability["id"]

        current = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "known_as_of": _parse(correction["recorded_at"]).isoformat(),
                "at": (now + timedelta(hours=3)).isoformat(),
            },
        )
        assert current.status_code == 200
        current_body = current.json()
        assert current_body["state"]["availability"]["id"] == correction["id"]
        authority = {row["id"]: row["authoritative_as_of"] for row in current_body["availability"]}
        assert authority[availability["id"]] is False
        assert authority[correction["id"]] is True

        unknown = client.get(
            "/v1/aircraft/00000000-0000-0000-0000-000000000999/timeline",
            params={"from": window_from.isoformat(), "to": window_to.isoformat()},
        )
        assert unknown.status_code == 404

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            event_types = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :aircraft_id ORDER BY aggregate_version"
                    ),
                    {"aircraft_id": UUID(aircraft_id)},
                )
                .scalars()
                .all()
            )
            assert event_types == [
                "AIRCRAFT_REGISTERED",
                "AIRCRAFT_POSITION_RECORDED",
                "AIRCRAFT_AVAILABILITY_CHANGED",
                "AIRCRAFT_AVAILABILITY_CHANGED",
            ]
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_overlapping_availability_writes_cannot_both_commit() -> None:
    settings = _settings()
    now = datetime.now(UTC)

    with TestClient(create_app(settings)) as client:
        aircraft_id, _ = _create_aircraft(client, suffix="B", icao="PRTB", iata="PRB")
        barrier = Barrier(2)

        def write(key: str, status_value: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/aircraft/{aircraft_id}/availability",
                headers={"Idempotency-Key": key},
                json={
                    "valid_from": (now + timedelta(hours=1)).isoformat(),
                    "valid_to": (now + timedelta(hours=5)).isoformat(),
                    "status": status_value,
                    "source": "concurrency-test",
                },
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(write, "pr4-race-1", "available")
            second = executor.submit(write, "pr4-race-2", "reserved")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [201, 409]

        timeline = client.get(
            f"/v1/aircraft/{aircraft_id}/timeline",
            params={
                "from": now.isoformat(),
                "to": (now + timedelta(hours=6)).isoformat(),
                "at": (now + timedelta(hours=2)).isoformat(),
            },
        )
        assert timeline.status_code == 200
        assert len(timeline.json()["availability"]) == 1
        assert timeline.json()["state"]["availability"] is not None
