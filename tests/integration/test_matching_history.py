from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from apps.api.main import create_app
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from tests.integration.matching_support import (
    insert_profile,
    settings,
    setup_matching_state,
)


@pytest.mark.integration
@pytest.mark.regression
def test_matching_is_deterministic_explainable_and_no_hindsight_safe() -> None:
    settings_value = settings()
    with TestClient(create_app(settings_value)) as client:
        (
            mission_id,
            aircraft_id,
            departure,
            aircraft_type_id,
            availability_id,
        ) = setup_matching_state(client, suffix="IA")
        insert_profile(
            settings_value,
            aircraft_type_id,
            recorded_at=datetime.now(UTC),
        )
        cutoff = datetime.now(UTC)

        first = client.get(
            f"/v1/missions/{mission_id}/matches",
            params={"known_as_of": cutoff.isoformat()},
        )
        second = client.get(
            f"/v1/missions/{mission_id}/matches",
            params={"known_as_of": cutoff.isoformat()},
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

        body = first.json()
        assert body["policy_version"] == "matching-v1"
        assert body["reference_currency"] == "EUR"
        assert body["feasible_count"] >= 1
        match = next(
            item for item in body["matches"] if item["aircraft_id"] == aircraft_id
        )
        assert match["reason_codes"][0] == "feasible"
        assert match["reference_profile"]["source"] == "validated-test-reference"
        assert match["position"]["recorded_at"] <= body["known_as_of"]
        assert match["score"]["total_basis_points"] == sum(
            match["score"][key]
            for key in (
                "deadhead_points",
                "operating_cost_points",
                "timing_buffer_points",
                "schedule_risk_points",
            )
        )

        correction = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr6-late-availability-correction"},
            json={
                "valid_from": (departure - timedelta(hours=1)).isoformat(),
                "valid_to": (departure + timedelta(hours=3)).isoformat(),
                "status": "reserved",
                "source": "pr6-late-correction",
                "supersedes_id": availability_id,
            },
        )
        assert correction.status_code == 201

        historical_k1 = client.get(
            f"/v1/missions/{mission_id}/matches",
            params={"known_as_of": cutoff.isoformat()},
        )
        assert historical_k1.status_code == 200
        assert aircraft_id in {
            item["aircraft_id"] for item in historical_k1.json()["matches"]
        }

        current_reserved = client.get(f"/v1/missions/{mission_id}/matches")
        assert current_reserved.status_code == 200
        assert aircraft_id not in {
            item["aircraft_id"] for item in current_reserved.json()["matches"]
        }

        restored = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": "pr6-availability-restored"},
            json={
                "valid_from": (departure - timedelta(hours=1)).isoformat(),
                "valid_to": (departure + timedelta(hours=3)).isoformat(),
                "status": "available",
                "source": "pr6-restoration",
                "supersedes_id": correction.json()["id"],
            },
        )
        assert restored.status_code == 201
        cutoff_two = datetime.now(UTC)

        historical_k2_before = client.get(
            f"/v1/missions/{mission_id}/matches",
            params={"known_as_of": cutoff_two.isoformat()},
        )
        assert historical_k2_before.status_code == 200
        assert aircraft_id in {
            item["aircraft_id"] for item in historical_k2_before.json()["matches"]
        }

        far_airport = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "pr6-far-airport"},
            json={
                "icao": "QXFA",
                "iata": "QXF",
                "lat": "40.6413",
                "lon": "-73.7781",
                "timezone": "America/New_York",
            },
        )
        assert far_airport.status_code == 201

        late_position = client.post(
            f"/v1/aircraft/{aircraft_id}/positions",
            headers={"Idempotency-Key": "pr6-late-position"},
            json={
                "airport_id": far_airport.json()["id"],
                "event_time": (cutoff_two - timedelta(minutes=1)).isoformat(),
                "source": "pr6-late-position",
                "provenance": {"late_observation": True},
            },
        )
        assert late_position.status_code == 201

        historical_k2_after = client.get(
            f"/v1/missions/{mission_id}/matches",
            params={"known_as_of": cutoff_two.isoformat()},
        )
        assert historical_k2_after.status_code == 200
        assert historical_k2_after.json() == historical_k2_before.json()

        current_after_late_position = client.get(
            f"/v1/missions/{mission_id}/matches"
        )
        assert current_after_late_position.status_code == 200
        assert aircraft_id not in {
            item["aircraft_id"]
            for item in current_after_late_position.json()["matches"]
        }

        missing = client.get(
            "/v1/missions/00000000-0000-0000-0000-000000000999/matches"
        )
        assert missing.status_code == 404

    engine = create_engine(settings_value.database_url)
    try:
        with Session(engine) as session:
            outbox_count = session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.aggregate_id == UUID(mission_id))
            )
            assert outbox_count == 2
    finally:
        engine.dispose()
