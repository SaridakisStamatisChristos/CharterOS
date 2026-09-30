from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.test_pr38_aircraft_capacity import (
    _create_quote,
    _setup_shared_aircraft,
)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _award(client: TestClient, quote_id: str, suffix: str) -> Response:
    return cast(
        Response,
        client.post(
            f"/v1/quotes/{quote_id}/accept",
            headers={"Idempotency-Key": f"pr42-award-{suffix}"},
        ),
    )


def _assert_failed_award_is_atomic(
    settings: Settings,
    *,
    mission_id: str,
    quote_id: str,
) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM aircraft_capacity_reservations "
                        "WHERE mission_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_evidence_snapshots "
                        "WHERE decision_type = 'award_commit' "
                        "AND subject_type = 'mission' AND subject_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE (aggregate_id = :mission_id AND event_type = 'MISSION_SELECTED') "
                        "OR (aggregate_id = :quote_id AND event_type = 'QUOTE_ACCEPTED')"
                    ),
                    {
                        "mission_id": UUID(mission_id),
                        "quote_id": UUID(quote_id),
                    },
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr42_successful_award_persists_canonical_decision_evidence() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=7)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="EV")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="EV1",
            departure=departure,
        )

        response = _award(client, quote_id, "EV")
        assert response.status_code == 201, response.text
        booking_id = str(response.json()["id"])

        evidence = client.get(
            f"/v1/evidence/bookings/{booking_id}",
            headers={"X-Buyer-Id": shared["buyer_id"]},
        )
        assert evidence.status_code == 200, evidence.text
        decisions = [
            item for item in evidence.json()["decisions"] if item["decision_type"] == "award_commit"
        ]
        assert len(decisions) == 1
        decision = decisions[0]
        assert decision["known_as_of"] == decision["decided_at"]
        assert decision["policy_versions"] == {
            "award_revalidation": "award-truth-gate-v1",
            "matching": "matching-v1",
            "capacity": "aircraft-capacity-v1",
        }
        assert decision["content"]["feasible"] is True
        assert decision["content"]["mission_id"] == mission_id
        assert decision["content"]["quote_id"] == quote_id
        assert decision["content"]["aircraft_id"] == shared["aircraft_id"]
        assert decision["content"]["operator_id"] == shared["operator_id"]
        assert decision["content"]["position"]["id"]
        assert decision["content"]["availability"]["status"] == "available"
        assert decision["content"]["reference_profile"]["id"]
        assert decision["content"]["capacity_interval"]["start"]
        assert decision["content"]["capacity_interval"]["end"]

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT decided_at, known_as_of, policy_versions, canonical_json "
                        "FROM decision_evidence_snapshots "
                        "WHERE decision_type = 'award_commit' "
                        "AND source_aggregate_type = 'booking' "
                        "AND source_aggregate_id = :booking_id"
                    ),
                    {"booking_id": UUID(booking_id)},
                )
                .mappings()
                .one()
            )
            assert row["decided_at"] == row["known_as_of"]
            assert row["policy_versions"]["matching"] == "matching-v1"
            canonical = json.loads(str(row["canonical_json"]))
            assert canonical["content"]["mission_version"] >= 1
            assert canonical["content"]["quote_revision_number"] >= 1
            assert canonical["content"]["aircraft_version"] >= 1
            assert canonical["content"]["operator_version"] >= 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr42_aircraft_maintenance_after_quote_fails_closed_atomically() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=8)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AM")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="AM1",
            departure=departure,
        )
        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE aircraft SET status = 'maintenance', version = version + 1 "
                        "WHERE id = :aircraft_id"
                    ),
                    {"aircraft_id": UUID(shared["aircraft_id"])},
                )
        finally:
            engine.dispose()

        response = _award(client, quote_id, "AM")
        assert response.status_code == 409
        assert "aircraft_inactive" in response.text
        assert client.get(f"/v1/quotes/{quote_id}").json()["status"] == "submitted"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "sourcing"

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)


@pytest.mark.integration
def test_pr42_operator_insurance_invalid_after_quote_fails_closed_atomically() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=9)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="OI")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="OI1",
            departure=departure,
        )
        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE operators SET insurance_status = 'expired', version = version + 1 "
                        "WHERE id = :operator_id"
                    ),
                    {"operator_id": UUID(shared["operator_id"])},
                )
        finally:
            engine.dispose()

        response = _award(client, quote_id, "OI")
        assert response.status_code == 409
        assert "operator_insurance_invalid" in response.text

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)


@pytest.mark.integration
def test_pr42_aircraft_operator_lineage_change_is_rejected() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=10)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AL")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="AL1",
            departure=departure,
        )
        organization = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "pr42-lineage-org"},
            json={
                "type": "operator",
                "legal_name": "PR42 Lineage Operator",
                "country": "GR",
            },
        )
        assert organization.status_code == 201
        operator = client.post(
            "/v1/operators",
            headers={"Idempotency-Key": "pr42-lineage-operator"},
            json={
                "organization_id": organization.json()["id"],
                "aoc_reference": "GR-PR42-LINEAGE",
                "operating_regions": ["EU"],
                "verification_status": "verified",
                "insurance_status": "valid",
                "commercial_status": "active",
            },
        )
        assert operator.status_code == 201

        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE aircraft SET operator_id = :operator_id, version = version + 1 "
                        "WHERE id = :aircraft_id"
                    ),
                    {
                        "operator_id": UUID(str(operator.json()["id"])),
                        "aircraft_id": UUID(shared["aircraft_id"]),
                    },
                )
        finally:
            engine.dispose()

        response = _award(client, quote_id, "AL")
        assert response.status_code == 409
        assert "no longer belongs to the quoted operator" in response.text

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)


@pytest.mark.integration
def test_pr42_authoritative_availability_change_blocks_award() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=11)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AV")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="AV1",
            departure=departure,
        )

        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                current = (
                    connection.execute(
                        text(
                            "SELECT id, valid_from, valid_to "
                            "FROM aircraft_availability_records "
                            "WHERE aircraft_id = :aircraft_id "
                            "ORDER BY recorded_at DESC, id DESC LIMIT 1"
                        ),
                        {"aircraft_id": UUID(shared["aircraft_id"])},
                    )
                    .mappings()
                    .one()
                )
        finally:
            engine.dispose()

        correction = client.post(
            f"/v1/aircraft/{shared['aircraft_id']}/availability",
            headers={"Idempotency-Key": "pr42-availability-reserved"},
            json={
                "valid_from": current["valid_from"].isoformat(),
                "valid_to": current["valid_to"].isoformat(),
                "status": "reserved",
                "source": "pr42-award-correction",
                "supersedes_id": str(current["id"]),
                "provenance": {"fixture": "pr42", "reason": "reserved-before-award"},
            },
        )
        assert correction.status_code == 201

        response = _award(client, quote_id, "AV")
        assert response.status_code == 409
        assert "not_available" in response.text

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)


@pytest.mark.integration
def test_pr42_current_position_can_make_previously_quoted_tail_infeasible() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=12)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AP")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="AP1",
            departure=departure,
        )
        far_airport = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "pr42-far-airport"},
            json={
                "icao": "KJFK",
                "iata": "JFK",
                "lat": "40.6413",
                "lon": "-73.7781",
                "timezone": "America/New_York",
            },
        )
        assert far_airport.status_code == 201
        position = client.post(
            f"/v1/aircraft/{shared['aircraft_id']}/positions",
            headers={"Idempotency-Key": "pr42-far-position"},
            json={
                "airport_id": far_airport.json()["id"],
                "event_time": datetime.now(UTC).isoformat(),
                "source": "pr42-award-position",
                "provenance": {"fixture": "pr42", "reason": "far-before-award"},
            },
        )
        assert position.status_code == 201

        response = _award(client, quote_id, "AP")
        assert response.status_code == 409
        assert "reposition_too_far" in response.text

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)


@pytest.mark.integration
def test_pr42_failed_approved_award_preserves_historical_approval_evidence() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=13)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AH")
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="AH1",
            departure=departure,
        )
        approval = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/{quote_id}/approve",
            headers={
                "X-Buyer-Id": shared["buyer_id"],
                "Idempotency-Key": "pr42-approval-history",
            },
            json={"note": "Operationally feasible when approved"},
        )
        assert approval.status_code == 201, approval.text
        approval_id = str(approval.json()["id"])

        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE aircraft SET status = 'maintenance', version = version + 1 "
                        "WHERE id = :aircraft_id"
                    ),
                    {"aircraft_id": UUID(shared["aircraft_id"])},
                )
        finally:
            engine.dispose()

        award = client.post(
            f"/v1/buyer-portal/approvals/{approval_id}/award",
            headers={
                "X-Buyer-Id": shared["buyer_id"],
                "Idempotency-Key": "pr42-approved-award-failure",
            },
        )
        assert award.status_code == 409
        assert "aircraft_inactive" in award.text

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            approval_row = (
                connection.execute(
                    text(
                        "SELECT status, booking_id FROM procurement_approvals "
                        "WHERE id = :approval_id"
                    ),
                    {"approval_id": UUID(approval_id)},
                )
                .mappings()
                .one()
            )
            assert approval_row["status"] == "approved"
            assert approval_row["booking_id"] is None
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_evidence_snapshots "
                        "WHERE decision_type = 'quote_comparison' "
                        "AND source_aggregate_type = 'procurement_approval' "
                        "AND source_aggregate_id = :approval_id"
                    ),
                    {"approval_id": UUID(approval_id)},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()

    _assert_failed_award_is_atomic(settings, mission_id=mission_id, quote_id=quote_id)
