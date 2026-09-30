import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.capacity_support import seed_capacity_reference_profile
from tests.integration.test_pr23_disruptions import _booked_operation


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _open_disruption(
    client: TestClient,
    *,
    booking_id: str,
    operator_id: str,
    suffix: str,
) -> str:
    response = client.post(
        f"/v1/bookings/{booking_id}/disruptions",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr40-disruption-{suffix}",
        },
        json={
            "disruption_type": "aircraft_unavailable",
            "reason": "PR40 canonical replacement feasibility exercise",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_tail(
    client: TestClient,
    *,
    operator_id: str,
    origin_id: str,
    departure: datetime,
    suffix: str,
    seat_capacity: int = 72,
    range_nm: int = 2800,
    status: str = "active",
    position_airport_id: str | None = None,
    add_position: bool = True,
    availability_status: str | None = "available",
    availability_from: datetime | None = None,
    availability_to: datetime | None = None,
    seed_profile: bool = True,
) -> str:
    response = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr40-aircraft-{suffix}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-{suffix}",
            "aircraft_type": {
                "manufacturer": "PR40 Airframes",
                "model": f"FeasibilityJet {suffix}",
                "category": "regional",
                "seats_min": 1,
                "seats_max": 100,
                "range_nm": max(range_nm, 1),
                "runway_requirements": {},
                "baggage_cargo_profile": {},
            },
            "seat_capacity": seat_capacity,
            "cargo_capacity": "1000",
            "range_nm": range_nm,
            "home_base": origin_id,
            "status": status,
        },
    )
    assert response.status_code == 201
    aircraft_id = str(response.json()["id"])
    if seed_profile:
        seed_capacity_reference_profile(
            response.json(),
            source=f"pr40-capacity-{suffix}",
        )

    if add_position:
        position = client.post(
            f"/v1/aircraft/{aircraft_id}/positions",
            headers={"Idempotency-Key": f"pr40-position-{suffix}"},
            json={
                "airport_id": position_airport_id or origin_id,
                "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "source": "pr40-operations",
                "provenance": {"fixture": "pr40", "suffix": suffix},
            },
        )
        assert position.status_code == 201

    if availability_status is not None:
        availability = client.post(
            f"/v1/aircraft/{aircraft_id}/availability",
            headers={"Idempotency-Key": f"pr40-availability-{suffix}"},
            json={
                "valid_from": (
                    availability_from or departure - timedelta(hours=3)
                ).isoformat(),
                "valid_to": (
                    availability_to or departure + timedelta(hours=6)
                ).isoformat(),
                "status": availability_status,
                "source": "pr40-operations",
                "provenance": {"fixture": "pr40", "suffix": suffix},
            },
        )
        assert availability.status_code == 201
    return aircraft_id


def _propose(
    client: TestClient,
    *,
    disruption_id: str,
    operator_id: str,
    aircraft_id: str,
    suffix: str,
    departure_window: tuple[datetime, datetime] | None = None,
) -> Response:
    body: dict[str, object] = {
        "proposed_aircraft_id": aircraft_id,
        "source": "dispatch",
        "source_evidence": "PR40 canonical feasibility review",
    }
    if departure_window is not None:
        body["departure_window"] = {
            "start": departure_window[0].isoformat(),
            "end": departure_window[1].isoformat(),
        }
    return client.post(
        f"/v1/disruptions/{disruption_id}/replacement-options",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr40-proposal-{suffix}",
        },
        json=body,
    )


@pytest.mark.integration
def test_pr40_feasible_same_operator_replacement_captures_reproducible_evidence_without_hold() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="F1")
        disruption_id = _open_disruption(
            client,
            booking_id=str(setup["booking_id"]),
            operator_id=str(setup["operator_id"]),
            suffix="F1",
        )
        replacement_aircraft_id = str(setup["replacement_aircraft_id"])
        response = _propose(
            client,
            disruption_id=disruption_id,
            operator_id=str(setup["operator_id"]),
            aircraft_id=replacement_aircraft_id,
            suffix="F1",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["feasibility_policy_version"] == "matching-v1"
        assert body["feasibility_known_as_of"] is not None
        assert body["position_observation_id"] is not None
        assert body["position_event_time"] is not None
        assert body["position_recorded_at"] is not None
        assert body["availability_record_id"] is not None
        assert body["availability_recorded_at"] is not None
        assert body["reference_profile_id"] is not None
        assert body["reference_profile_recorded_at"] is not None
        assert body["route_distance_tenths_nm"] > 0
        assert body["required_range_nm"] > 0
        assert body["reposition_distance_tenths_nm"] >= 0
        assert body["route_minutes"] > 0
        assert body["reposition_minutes"] >= 0
        assert body["timing_buffer_minutes"] >= 0

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            held = connection.execute(
                text(
                    "SELECT count(*) FROM aircraft_capacity_reservations "
                    "WHERE aircraft_id = :aircraft_id"
                ),
                {"aircraft_id": UUID(replacement_aircraft_id)},
            ).scalar_one()
            assert held == 0
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "suffix",
        "seat_capacity",
        "range_nm",
        "status",
        "add_position",
        "availability_status",
        "seed_profile",
        "expected_reason",
    ),
    [
        ("S1", 20, 2800, "active", True, "available", True, "insufficient_capacity"),
        ("S2", 72, 100, "active", True, "available", True, "insufficient_range"),
        ("S3", 72, 2800, "maintenance", True, "available", True, "aircraft_inactive"),
        ("S4", 72, 2800, "active", False, "available", True, "no_position"),
        ("S5", 72, 2800, "active", True, None, True, "no_availability"),
        ("S6", 72, 2800, "active", True, "reserved", True, "not_available"),
        ("S7", 72, 2800, "active", True, "available", False, "no_reference_profile"),
    ],
)
def test_pr40_replacement_rejects_canonical_matching_failures(
    suffix: str,
    seat_capacity: int,
    range_nm: int,
    status: str,
    add_position: bool,
    availability_status: str | None,
    seed_profile: bool,
    expected_reason: str,
) -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix=suffix)
        departure = setup["departure"]
        assert isinstance(departure, datetime)
        aircraft_id = _create_tail(
            client,
            operator_id=str(setup["operator_id"]),
            origin_id=str(setup["origin_id"]),
            departure=departure,
            suffix=f"X{suffix}",
            seat_capacity=seat_capacity,
            range_nm=range_nm,
            status=status,
            add_position=add_position,
            availability_status=availability_status,
            seed_profile=seed_profile,
        )
        disruption_id = _open_disruption(
            client,
            booking_id=str(setup["booking_id"]),
            operator_id=str(setup["operator_id"]),
            suffix=suffix,
        )
        response = _propose(
            client,
            disruption_id=disruption_id,
            operator_id=str(setup["operator_id"]),
            aircraft_id=aircraft_id,
            suffix=suffix,
        )
        assert response.status_code == 409
        assert expected_reason in response.text


@pytest.mark.integration
@pytest.mark.parametrize(
    ("suffix", "column", "value", "expected_reason"),
    [
        ("O1", "verification_status", "pending", "operator_unverified"),
        ("O2", "insurance_status", "expired", "operator_insurance_invalid"),
        ("O3", "commercial_status", "suspended", "operator_commercial_inactive"),
    ],
)
def test_pr40_replacement_revalidates_operator_eligibility(
    suffix: str,
    column: str,
    value: str,
    expected_reason: str,
) -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix=suffix)
        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE operators SET {column} = :value, version = version + 1 "
                        "WHERE id = :operator_id"
                    ),
                    {
                        "value": value,
                        "operator_id": UUID(str(setup["operator_id"])),
                    },
                )
        finally:
            engine.dispose()

        disruption_id = _open_disruption(
            client,
            booking_id=str(setup["booking_id"]),
            operator_id=str(setup["operator_id"]),
            suffix=suffix,
        )
        response = _propose(
            client,
            disruption_id=disruption_id,
            operator_id=str(setup["operator_id"]),
            aircraft_id=str(setup["replacement_aircraft_id"]),
            suffix=suffix,
        )
        assert response.status_code == 409
        assert expected_reason in response.text


@pytest.mark.integration
def test_pr40_replacement_rejects_reposition_too_far_and_too_late() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        far_setup = _booked_operation(client, suffix="R1")
        far_departure = far_setup["departure"]
        assert isinstance(far_departure, datetime)
        far_airport = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "pr40-far-airport-r1"},
            json={
                "icao": "QFAR",
                "iata": None,
                "lat": "40.6413",
                "lon": "-73.7781",
                "timezone": "America/New_York",
            },
        )
        assert far_airport.status_code == 201
        far_tail = _create_tail(
            client,
            operator_id=str(far_setup["operator_id"]),
            origin_id=str(far_setup["origin_id"]),
            departure=far_departure,
            suffix="XR1",
            position_airport_id=str(far_airport.json()["id"]),
        )
        far_disruption = _open_disruption(
            client,
            booking_id=str(far_setup["booking_id"]),
            operator_id=str(far_setup["operator_id"]),
            suffix="R1",
        )
        far_response = _propose(
            client,
            disruption_id=far_disruption,
            operator_id=str(far_setup["operator_id"]),
            aircraft_id=far_tail,
            suffix="R1",
        )
        assert far_response.status_code == 409
        assert "reposition_too_far" in far_response.text

        late_setup = _booked_operation(client, suffix="R2")
        late_departure = late_setup["departure"]
        assert isinstance(late_departure, datetime)
        now = datetime.now(UTC)
        window = (now + timedelta(minutes=10), now + timedelta(minutes=30))
        late_tail = _create_tail(
            client,
            operator_id=str(late_setup["operator_id"]),
            origin_id=str(late_setup["origin_id"]),
            departure=late_departure,
            suffix="XR2",
            position_airport_id=str(late_setup["destination_id"]),
            availability_from=now - timedelta(hours=1),
            availability_to=now + timedelta(hours=2),
        )
        late_disruption = _open_disruption(
            client,
            booking_id=str(late_setup["booking_id"]),
            operator_id=str(late_setup["operator_id"]),
            suffix="R2",
        )
        late_response = _propose(
            client,
            disruption_id=late_disruption,
            operator_id=str(late_setup["operator_id"]),
            aircraft_id=late_tail,
            suffix="R2",
            departure_window=window,
        )
        assert late_response.status_code == 409
        assert "reposition_too_late" in late_response.text


@pytest.mark.integration
def test_pr40_late_availability_correction_preserves_proposal_evidence_and_blocks_resolution() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="H1")
        departure = setup["departure"]
        assert isinstance(departure, datetime)
        disruption_id = _open_disruption(
            client,
            booking_id=str(setup["booking_id"]),
            operator_id=str(setup["operator_id"]),
            suffix="H1",
        )
        proposal = _propose(
            client,
            disruption_id=disruption_id,
            operator_id=str(setup["operator_id"]),
            aircraft_id=str(setup["replacement_aircraft_id"]),
            suffix="H1",
        )
        assert proposal.status_code == 201
        proposal_body = proposal.json()

        approval = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": str(setup["buyer_id"]),
                "Idempotency-Key": "pr40-approve-h1",
            },
            json={
                "proposal_id": proposal_body["id"],
                "decision": "approved",
            },
        )
        assert approval.status_code == 201

        correction = client.post(
            f"/v1/aircraft/{setup['replacement_aircraft_id']}/availability",
            headers={"Idempotency-Key": "pr40-late-correction-h1"},
            json={
                "valid_from": (departure - timedelta(hours=3)).isoformat(),
                "valid_to": (departure + timedelta(hours=6)).isoformat(),
                "status": "reserved",
                "source": "pr40-late-correction",
                "supersedes_id": proposal_body["availability_record_id"],
                "provenance": {"fixture": "pr40", "late_correction": True},
            },
        )
        assert correction.status_code == 201

        listed = client.get(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={"X-Buyer-Id": str(setup["buyer_id"])},
        )
        assert listed.status_code == 200
        historical = listed.json()["proposals"][0]
        assert historical["availability_record_id"] == proposal_body["availability_record_id"]
        assert historical["availability_recorded_at"] == proposal_body["availability_recorded_at"]
        assert historical["feasibility_known_as_of"] == proposal_body["feasibility_known_as_of"]
        assert historical["position_observation_id"] == proposal_body["position_observation_id"]
        assert historical["reference_profile_id"] == proposal_body["reference_profile_id"]

        resolved = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": str(setup["operator_id"]),
                "Idempotency-Key": "pr40-resolve-h1",
            },
            json={
                "proposal_id": proposal_body["id"],
                "outcome": "Must fail after authoritative availability correction",
            },
        )
        assert resolved.status_code == 409
        assert "not_available" in resolved.text


@pytest.mark.integration
def test_pr40_replacement_capacity_check_rejects_another_bookings_reserved_overlap() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="C1")
        departure = setup["departure"]
        assert isinstance(departure, datetime)

        second_mission = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr40-capacity-mission-c1"},
            json={
                "buyer_id": str(setup["buyer_id"]),
                "origin_airport_id": str(setup["origin_id"]),
                "destination_airport_id": str(setup["destination_id"]),
                "departure_window": {
                    "start": departure.isoformat(),
                    "end": (departure + timedelta(hours=2)).isoformat(),
                },
                "passenger_count": 20,
            },
        )
        assert second_mission.status_code == 201
        mission_id = str(second_mission.json()["id"])
        assert client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": "pr40-capacity-open-c1"},
        ).status_code == 200
        rfq = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": "pr40-capacity-rfq-c1"},
            json={
                "operator_id": str(setup["operator_id"]),
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert rfq.status_code == 201
        rfq_id = str(rfq.json()["id"])
        assert client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr40-capacity-ack-c1"},
        ).status_code == 200
        quote = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr40-capacity-quote-c1"},
            json={
                "aircraft_id": str(setup["replacement_aircraft_id"]),
                "currency": "EUR",
                "base_amount_minor": 7_500_000,
                "repositioning_amount_minor": 100_000,
                "price_components": [],
                "inclusions": [],
                "exclusions": [],
                "valid_until": (departure - timedelta(days=1)).isoformat(),
            },
        )
        assert quote.status_code == 201
        award = client.post(
            f"/v1/quotes/{quote.json()['id']}/accept",
            headers={"Idempotency-Key": "pr40-capacity-award-c1"},
        )
        assert award.status_code == 201

        disruption_id = _open_disruption(
            client,
            booking_id=str(setup["booking_id"]),
            operator_id=str(setup["operator_id"]),
            suffix="C1",
        )
        proposal = _propose(
            client,
            disruption_id=disruption_id,
            operator_id=str(setup["operator_id"]),
            aircraft_id=str(setup["replacement_aircraft_id"]),
            suffix="C1",
        )
        assert proposal.status_code == 409
        assert "overlapping committed CharterOS capacity" in proposal.text
