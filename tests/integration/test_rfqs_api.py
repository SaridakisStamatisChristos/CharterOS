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


def _create_operator(
    client: TestClient,
    *,
    suffix: str,
    verification_status: str = "verified",
    insurance_status: str = "valid",
    commercial_status: str = "active",
) -> str:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr7-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR7 Operator {suffix}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201

    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr7-operator-{suffix}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR7-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": verification_status,
            "insurance_status": insurance_status,
            "commercial_status": commercial_status,
        },
    )
    assert operator.status_code == 201
    return str(operator.json()["id"])


def _setup_open_mission(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, datetime, str]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr7-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR7 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201

    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr7-origin-{suffix}"},
        json={
            "icao": f"R{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr7-destination-{suffix}"},
        json={
            "icao": f"R{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    operator_id = _create_operator(client, suffix=suffix)
    departure = datetime.now(UTC) + timedelta(days=7)
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr7-mission-{suffix}"},
        json={
            "buyer_id": buyer.json()["id"],
            "origin_airport_id": origin.json()["id"],
            "destination_airport_id": destination.json()["id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=3)).isoformat(),
            },
            "passenger_count": 90,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])

    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr7-open-{suffix}"},
    )
    assert opened.status_code == 200
    return mission_id, departure, operator_id


@pytest.mark.integration
def test_rfq_create_list_acknowledge_decline_idempotency_and_outbox() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, departure, operator_id = _setup_open_mission(client, suffix="RA")
        body = {
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        }

        created = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-create-ra"},
            json=body,
        )
        assert created.status_code == 201
        rfq = created.json()
        rfq_id = rfq["id"]
        assert rfq["status"] == "sent"
        assert rfq["version"] == 2
        assert rfq["operator_id"] == operator_id

        replay = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-create-ra"},
            json=body,
        )
        assert replay.status_code == 201
        assert replay.json() == rfq

        changed_body = dict(body)
        changed_body["response_deadline"] = (departure - timedelta(days=1)).isoformat()
        key_conflict = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-create-ra"},
            json=changed_body,
        )
        assert key_conflict.status_code == 409

        duplicate = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-create-ra-duplicate"},
            json=body,
        )
        assert duplicate.status_code == 409

        mission = client.get(f"/v1/missions/{mission_id}")
        assert mission.status_code == 200
        assert mission.json()["status"] == "sourcing"
        assert mission.json()["version"] == 3

        listed = client.get(f"/v1/missions/{mission_id}/rfqs")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["rfqs"]] == [rfq_id]

        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr7-ack-ra"},
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "acknowledged"
        assert acknowledged.json()["version"] == 3

        ack_replay = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr7-ack-ra"},
        )
        assert ack_replay.status_code == 200
        assert ack_replay.json() == acknowledged.json()

        declined = client.post(
            f"/v1/rfqs/{rfq_id}/decline",
            headers={"Idempotency-Key": "pr7-decline-ra"},
            json={"reason": "  capacity   reallocated  "},
        )
        assert declined.status_code == 200
        assert declined.json()["status"] == "declined"
        assert declined.json()["version"] == 4
        assert declined.json()["decline_reason"] == "capacity reallocated"

        second_operator = _create_operator(client, suffix="RB")
        second = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-create-rb"},
            json={
                "operator_id": second_operator,
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert second.status_code == 201
        assert second.json()["status"] == "sent"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            mission_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :aggregate_id ORDER BY aggregate_version"
                    ),
                    {"aggregate_id": UUID(mission_id)},
                )
                .scalars()
                .all()
            )
            assert mission_events == [
                "MISSION_CREATED",
                "MISSION_OPENED",
                "MISSION_SOURCING",
            ]

            rfq_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :aggregate_id ORDER BY aggregate_version"
                    ),
                    {"aggregate_id": UUID(rfq_id)},
                )
                .scalars()
                .all()
            )
            assert rfq_events == [
                "RFQ_CREATED",
                "RFQ_SENT",
                "RFQ_ACKNOWLEDGED",
                "RFQ_DECLINED",
            ]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_ineligible_operator_does_not_move_mission_into_sourcing() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, departure, _ = _setup_open_mission(client, suffix="RC")
        ineligible = _create_operator(
            client,
            suffix="RD",
            verification_status="pending",
            insurance_status="valid",
        )

        rejected = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-ineligible-rd"},
            json={
                "operator_id": ineligible,
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert rejected.status_code == 409

        mission = client.get(f"/v1/missions/{mission_id}")
        assert mission.status_code == 200
        assert mission.json()["status"] == "open"
        assert mission.json()["version"] == 2

        listed = client.get(f"/v1/missions/{mission_id}/rfqs")
        assert listed.status_code == 200
        assert listed.json()["rfqs"] == []


@pytest.mark.integration
def test_expiry_is_explicit_idempotent_and_terminal() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, departure, operator_id = _setup_open_mission(client, suffix="RE")
        created = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-expire-create"},
            json={
                "operator_id": operator_id,
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert created.status_code == 201
        rfq_id = created.json()["id"]

        engine = create_engine(settings.database_url)
        try:
            now = datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE rfqs SET created_at = :created_at, sent_at = :sent_at, "
                        "response_deadline = :response_deadline WHERE id = :rfq_id"
                    ),
                    {
                        "created_at": now - timedelta(hours=3),
                        "sent_at": now - timedelta(hours=2),
                        "response_deadline": now - timedelta(hours=1),
                        "rfq_id": UUID(rfq_id),
                    },
                )
        finally:
            engine.dispose()

        expired = client.post(
            f"/v1/rfqs/{rfq_id}/expire",
            headers={"Idempotency-Key": "pr7-expire"},
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "expired"
        assert expired.json()["version"] == 3

        replay = client.post(
            f"/v1/rfqs/{rfq_id}/expire",
            headers={"Idempotency-Key": "pr7-expire"},
        )
        assert replay.status_code == 200
        assert replay.json() == expired.json()

        after_terminal = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr7-expired-ack"},
        )
        assert after_terminal.status_code == 409


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_acknowledge_attempts_only_one_transition_commits() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, departure, operator_id = _setup_open_mission(client, suffix="RF")
        created = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr7-race-create"},
            json={
                "operator_id": operator_id,
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert created.status_code == 201
        rfq_id = created.json()["id"]
        barrier = Barrier(2)

        def acknowledge(key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/rfqs/{rfq_id}/acknowledge",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(acknowledge, "pr7-race-ack-1")
            second = executor.submit(acknowledge, "pr7-race-ack-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [200, 409]
        listed = client.get(f"/v1/missions/{mission_id}/rfqs")
        assert listed.status_code == 200
        final = listed.json()["rfqs"][0]
        assert final["version"] == 3
        assert final["status"] == "acknowledged"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            versions: Sequence[int] = (
                connection.execute(
                    text(
                        "SELECT aggregate_version FROM outbox_events "
                        "WHERE aggregate_id = :aggregate_id ORDER BY aggregate_version"
                    ),
                    {"aggregate_id": UUID(rfq_id)},
                )
                .scalars()
                .all()
            )
            assert versions == [1, 2, 3]
    finally:
        engine.dispose()
