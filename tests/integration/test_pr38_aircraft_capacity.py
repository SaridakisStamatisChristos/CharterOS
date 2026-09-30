from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.capacity_support import seed_capacity_reference_profile


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _setup_shared_aircraft(
    client: TestClient,
    *,
    suffix: str,
    with_profile: bool = True,
) -> dict[str, str]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr38-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR38 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201

    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr38-origin-{suffix}"},
        json={
            "icao": f"K{suffix}A",
            "iata": f"K{suffix}",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr38-destination-{suffix}"},
        json={
            "icao": f"L{suffix}B",
            "iata": f"L{suffix}",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    operator_org = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr38-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR38 Operator {suffix}",
            "country": "GR",
        },
    )
    assert operator_org.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr38-operator-{suffix}"},
        json={
            "organization_id": operator_org.json()["id"],
            "aoc_reference": f"GR-PR38-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201

    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr38-aircraft-{suffix}"},
        json={
            "operator_id": operator.json()["id"],
            "registration": f"SX-R{suffix}",
            "aircraft_type": {
                "manufacturer": "PR38 Airframes",
                "model": f"CapacityJet {suffix}",
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
            "home_base": origin.json()["id"],
            "status": "active",
        },
    )
    assert aircraft.status_code == 201
    if with_profile:
        seed_capacity_reference_profile(
            aircraft.json(),
            source=f"pr38-capacity-{suffix}",
        )

    return {
        "buyer_id": str(buyer.json()["id"]),
        "origin_id": str(origin.json()["id"]),
        "destination_id": str(destination.json()["id"]),
        "operator_id": str(operator.json()["id"]),
        "aircraft_id": str(aircraft.json()["id"]),
    }


def _create_quote(
    client: TestClient,
    *,
    shared: dict[str, str],
    suffix: str,
    departure: datetime,
) -> tuple[str, str]:
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr38-mission-{suffix}"},
        json={
            "buyer_id": shared["buyer_id"],
            "origin_airport_id": shared["origin_id"],
            "destination_airport_id": shared["destination_id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=1)).isoformat(),
            },
            "passenger_count": 20,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr38-open-{suffix}"},
    )
    assert opened.status_code == 200

    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr38-rfq-{suffix}"},
        json={
            "operator_id": shared["operator_id"],
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    rfq_id = str(rfq.json()["id"])
    acknowledged = client.post(
        f"/v1/rfqs/{rfq_id}/acknowledge",
        headers={"Idempotency-Key": f"pr38-ack-{suffix}"},
    )
    assert acknowledged.status_code == 200

    quote = client.post(
        f"/v1/rfqs/{rfq_id}/quotes",
        headers={"Idempotency-Key": f"pr38-quote-{suffix}"},
        json={
            "aircraft_id": shared["aircraft_id"],
            "currency": "EUR",
            "base_amount_minor": 8_000_000,
            "valid_until": (departure - timedelta(days=1)).isoformat(),
        },
    )
    assert quote.status_code == 201
    return mission_id, str(quote.json()["id"])


def _reservation_rows(settings: Settings, aircraft_id: str) -> list[dict[str, object]]:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT id, booking_id, mission_id, aircraft_id, starts_at, ends_at,
                               status, policy_version, reference_profile_id,
                               route_distance_tenths_nm, route_minutes,
                               turnaround_buffer_minutes
                        FROM aircraft_capacity_reservations
                        WHERE aircraft_id = :aircraft_id
                        ORDER BY starts_at, id
                        """
                    ),
                    {"aircraft_id": UUID(aircraft_id)},
                ).mappings()
            ]
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr38_overlapping_cross_mission_awards_have_exactly_one_winner() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=7)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="OA")
        mission_a, quote_a = _create_quote(
            client,
            shared=shared,
            suffix="OA1",
            departure=departure,
        )
        mission_b, quote_b = _create_quote(
            client,
            shared=shared,
            suffix="OA2",
            departure=departure + timedelta(minutes=30),
        )
        barrier = Barrier(2)

        def award(quote_id: str, key: str) -> int:
            barrier.wait()
            return int(
                client.post(
                    f"/v1/quotes/{quote_id}/accept",
                    headers={"Idempotency-Key": key},
                ).status_code
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(award, quote_a, "pr38-overlap-a")
            second = executor.submit(award, quote_b, "pr38-overlap-b")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [201, 409]
        quote_states = {
            quote_a: client.get(f"/v1/quotes/{quote_a}").json()["status"],
            quote_b: client.get(f"/v1/quotes/{quote_b}").json()["status"],
        }
        assert sorted(quote_states.values()) == ["accepted", "submitted"]
        mission_states = {
            mission_a: client.get(f"/v1/missions/{mission_a}").json()["status"],
            mission_b: client.get(f"/v1/missions/{mission_b}").json()["status"],
        }
        assert sorted(mission_states.values()) == ["selected", "sourcing"]

    rows = _reservation_rows(settings, shared["aircraft_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "reserved"
    assert rows[0]["policy_version"] == "aircraft-capacity-v1"
    assert cast(int, rows[0]["route_minutes"]) > 0
    assert rows[0]["turnaround_buffer_minutes"] == 45

    loser_quote = next(
        quote_id for quote_id, quote_state in quote_states.items() if quote_state == "submitted"
    )
    loser_mission = mission_a if loser_quote == quote_a else mission_b
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM bookings WHERE mission_id IN (:mission_a, :mission_b)"
                    ),
                    {"mission_a": UUID(mission_a), "mission_b": UUID(mission_b)},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM aircraft_capacity_reservations "
                        "WHERE mission_id = :mission_id"
                    ),
                    {"mission_id": UUID(loser_mission)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE aggregate_id = :quote_id AND event_type = 'QUOTE_ACCEPTED'"
                    ),
                    {"quote_id": UUID(loser_quote)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE aggregate_id = :mission_id AND event_type = 'MISSION_SELECTED'"
                    ),
                    {"mission_id": UUID(loser_mission)},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr38_non_overlapping_same_aircraft_awards_both_succeed() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=8)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="NB")
        _, quote_a = _create_quote(
            client,
            shared=shared,
            suffix="NB1",
            departure=departure,
        )
        _, quote_b = _create_quote(
            client,
            shared=shared,
            suffix="NB2",
            departure=departure + timedelta(hours=5),
        )

        first = client.post(
            f"/v1/quotes/{quote_a}/accept",
            headers={"Idempotency-Key": "pr38-non-overlap-a"},
        )
        second = client.post(
            f"/v1/quotes/{quote_b}/accept",
            headers={"Idempotency-Key": "pr38-non-overlap-b"},
        )
        assert first.status_code == second.status_code == 201

    rows = _reservation_rows(settings, shared["aircraft_id"])
    assert len(rows) == 2
    assert all(row["status"] == "reserved" for row in rows)
    assert cast(datetime, rows[0]["ends_at"]) <= cast(datetime, rows[1]["starts_at"])


@pytest.mark.integration
def test_pr38_sequential_conflict_is_fully_atomic() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=9)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="AC")
        mission_a, quote_a = _create_quote(
            client,
            shared=shared,
            suffix="AC1",
            departure=departure,
        )
        mission_b, quote_b = _create_quote(
            client,
            shared=shared,
            suffix="AC2",
            departure=departure + timedelta(minutes=15),
        )

        winner = client.post(
            f"/v1/quotes/{quote_a}/accept",
            headers={"Idempotency-Key": "pr38-atomic-winner"},
        )
        loser = client.post(
            f"/v1/quotes/{quote_b}/accept",
            headers={"Idempotency-Key": "pr38-atomic-loser"},
        )
        assert winner.status_code == 201
        assert loser.status_code == 409
        assert client.get(f"/v1/quotes/{quote_b}").json()["status"] == "submitted"
        assert client.get(f"/v1/missions/{mission_b}").json()["status"] == "sourcing"
        assert client.get(f"/v1/missions/{mission_a}").json()["status"] == "selected"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_b)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM aircraft_capacity_reservations "
                        "WHERE mission_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_b)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE aggregate_id IN (:mission_id, :quote_id) "
                        "AND event_type IN ('MISSION_SELECTED','QUOTE_ACCEPTED','BOOKING_CREATED',"
                        "'AIRCRAFT_CAPACITY_RESERVED')"
                    ),
                    {"mission_id": UUID(mission_b), "quote_id": UUID(quote_b)},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr38_missing_reference_profile_fails_closed_without_partial_award() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=10)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="FP", with_profile=False)
        mission_id, quote_id = _create_quote(
            client,
            shared=shared,
            suffix="FP1",
            departure=departure,
        )
        response = client.post(
            f"/v1/quotes/{quote_id}/accept",
            headers={"Idempotency-Key": "pr38-fail-closed-profile"},
        )
        assert response.status_code == 409
        assert client.get(f"/v1/quotes/{quote_id}").json()["status"] == "submitted"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "sourcing"

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
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr38_postgresql_exclusion_constraint_is_present() -> None:
    settings = _settings()
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            definition: str = str(
                connection.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conname = 'ex_aircraft_capacity_reservations_reserved_overlap'
                        """
                    )
                ).scalar_one()
            )
        normalized = " ".join(definition.split()).lower()
        assert "exclude using gist" in normalized
        assert "aircraft_id with =" in normalized
        assert "occupied_range with &&" in normalized
        assert "status" in normalized and "reserved" in normalized
    finally:
        engine.dispose()
