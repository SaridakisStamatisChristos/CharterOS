import os
from collections.abc import Sequence
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


def _create_foundation(client: TestClient, *, suffix: str) -> tuple[str, str, str]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr5-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR5 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201

    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr5-origin-{suffix}"},
        json={
            "icao": f"P{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == 201
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr5-destination-{suffix}"},
        json={
            "icao": f"P{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert destination.status_code == 201
    return buyer.json()["id"], origin.json()["id"], destination.json()["id"]


def _mission_body(buyer_id: str, origin_id: str, destination_id: str) -> dict[str, object]:
    start = datetime.now(UTC) + timedelta(days=7)
    return {
        "buyer_id": buyer_id,
        "origin_airport_id": origin_id,
        "destination_airport_id": destination_id,
        "departure_window": {
            "start": start.isoformat(),
            "end": (start + timedelta(hours=2)).isoformat(),
        },
        "passenger_count": 84,
        "max_budget": {"amount_minor": 9_500_000, "currency": "eur"},
        "special_requirements": ["Sports equipment", " sports   equipment ", "Hot catering"],
    }


@pytest.mark.integration
def test_mission_create_get_open_idempotency_and_outbox() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id, origin_id, destination_id = _create_foundation(client, suffix="XA")
        body = _mission_body(buyer_id, origin_id, destination_id)

        created = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr5-mission-create-a"},
            json=body,
        )
        assert created.status_code == 201
        mission = created.json()
        mission_id = mission["id"]
        UUID(mission_id)
        assert mission["version"] == 1
        assert mission["status"] == "draft"
        assert mission["max_budget"] == {"amount_minor": 9_500_000, "currency": "EUR"}
        assert mission["special_requirements"] == ["Sports equipment", "Hot catering"]

        replay = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr5-mission-create-a"},
            json=body,
        )
        assert replay.status_code == 201
        assert replay.json() == mission

        conflicting_body = dict(body)
        conflicting_body["passenger_count"] = 85
        key_conflict = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr5-mission-create-a"},
            json=conflicting_body,
        )
        assert key_conflict.status_code == 409

        fetched = client.get(f"/v1/missions/{mission_id}")
        assert fetched.status_code == 200
        assert fetched.json() == mission

        opened = client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": "pr5-mission-open-a"},
        )
        assert opened.status_code == 200
        assert opened.json()["status"] == "open"
        assert opened.json()["version"] == 2

        open_replay = client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": "pr5-mission-open-a"},
        )
        assert open_replay.status_code == 200
        assert open_replay.json() == opened.json()

        second_open = client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": "pr5-mission-open-b"},
        )
        assert second_open.status_code == 409

        unknown = client.get("/v1/missions/00000000-0000-0000-0000-000000000999")
        assert unknown.status_code == 404

        same_airport = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr5-mission-same-airport"},
            json=_mission_body(buyer_id, origin_id, origin_id),
        )
        assert same_airport.status_code == 422

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            event_types: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :mission_id ORDER BY aggregate_version"
                    ),
                    {"mission_id": UUID(mission_id)},
                )
                .scalars()
                .all()
            )
            assert event_types == ["MISSION_CREATED", "MISSION_OPENED"]
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_mission_open_attempts_only_advance_once() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id, origin_id, destination_id = _create_foundation(client, suffix="XB")
        created = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr5-race-create"},
            json=_mission_body(buyer_id, origin_id, destination_id),
        )
        assert created.status_code == 201
        mission_id = created.json()["id"]
        barrier = Barrier(2)

        def open_once(key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/missions/{mission_id}/open",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(open_once, "pr5-race-open-1")
            second = executor.submit(open_once, "pr5-race-open-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [200, 409]
        fetched = client.get(f"/v1/missions/{mission_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "open"
        assert fetched.json()["version"] == 2
