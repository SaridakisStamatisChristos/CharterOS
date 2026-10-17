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
from tests.integration.capacity_support import seed_capacity_reference_profile


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _setup_booking(client: TestClient, *, suffix: str) -> tuple[str, str, str, str]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr12-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR12 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201
    buyer_id = str(buyer.json()["id"])

    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr12-origin-{suffix}"},
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
        headers={"Idempotency-Key": f"pr12-destination-{suffix}"},
        json={
            "icao": f"R{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    operator_org = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr12-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR12 Operator {suffix}",
            "country": "GR",
        },
    )
    assert operator_org.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr12-operator-{suffix}"},
        json={
            "organization_id": operator_org.json()["id"],
            "aoc_reference": f"GR-PR12-{suffix}",
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
        headers={"Idempotency-Key": f"pr12-aircraft-{suffix}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-C{suffix}",
            "aircraft_type": {
                "manufacturer": "PR12 Airframes",
                "model": f"ContractJet {suffix}",
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
    seed_capacity_reference_profile(
        aircraft.json(),
        source=f"pr12-capacity-{suffix}",
    )
    aircraft_id = str(aircraft.json()["id"])
    departure = datetime.now(UTC) + timedelta(days=7)
    position = client.post(
        f"/v1/aircraft/{aircraft_id}/positions",
        headers={"Idempotency-Key": f"pr12-position-{suffix}"},
        json={
            "airport_id": origin.json()["id"],
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr12-award-fixture",
            "provenance": {"fixture": "pr12"},
        },
    )
    assert position.status_code == 201
    availability = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": f"pr12-availability-{suffix}"},
        json={
            "valid_from": (departure - timedelta(hours=3)).isoformat(),
            "valid_to": (departure + timedelta(hours=6)).isoformat(),
            "status": "available",
            "source": "pr12-award-fixture",
            "provenance": {"fixture": "pr12"},
        },
    )
    assert availability.status_code == 201

    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr12-mission-{suffix}"},
        json={
            "buyer_id": buyer_id,
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
    assert (
        client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": f"pr12-open-{suffix}"},
        ).status_code
        == 200
    )

    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr12-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    rfq_id = str(rfq.json()["id"])
    assert (
        client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": f"pr12-ack-{suffix}"},
        ).status_code
        == 200
    )

    quote = client.post(
        f"/v1/rfqs/{rfq_id}/quotes",
        headers={"Idempotency-Key": f"pr12-quote-{suffix}"},
        json={
            "aircraft_id": aircraft.json()["id"],
            "currency": "EUR",
            "base_amount_minor": 8_000_000,
            "valid_until": (departure - timedelta(days=1)).isoformat(),
        },
    )
    assert quote.status_code == 201

    booking = client.post(
        f"/v1/quotes/{quote.json()['id']}/accept",
        headers={"Idempotency-Key": f"pr12-award-{suffix}"},
    )
    assert booking.status_code == 201
    return str(booking.json()["id"]), mission_id, buyer_id, operator_id


def _contract_body(version: int = 1) -> dict[str, object]:
    return {
        "document_reference": "object://contracts/group-charter-v1.pdf",
        "document_version": version,
        "metadata": {
            "Jurisdiction": "GR",
            "Template": "Group Charter",
        },
    }


@pytest.mark.integration
def test_contract_creation_and_bilateral_acceptance_are_idempotent_and_auditable() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, mission_id, buyer_id, operator_id = _setup_booking(client, suffix="DA")

        created = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-da"},
            json=_contract_body(),
        )
        assert created.status_code == 201
        contract = created.json()
        contract_id = str(contract["id"])
        assert contract["booking_id"] == booking_id
        assert contract["buyer_id"] == buyer_id
        assert contract["operator_id"] == operator_id
        assert contract["document_version"] == 1
        assert contract["metadata"] == {
            "Jurisdiction": "GR",
            "Template": "Group Charter",
        }
        assert contract["status"] == "pending_acceptance"
        assert contract["version"] == 1
        assert contract["buyer_signed_at"] is None
        assert contract["operator_signed_at"] is None
        assert contract["accepted_at"] is None

        replay = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-da"},
            json=_contract_body(),
        )
        assert replay.status_code == 201
        assert replay.json() == contract

        key_reuse = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-da"},
            json=_contract_body(version=2),
        )
        assert key_reuse.status_code == 409

        duplicate_contract = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-da-duplicate"},
            json=_contract_body(),
        )
        assert duplicate_contract.status_code == 409

        by_id = client.get(f"/v1/contracts/{contract_id}")
        by_booking = client.get(f"/v1/bookings/{booking_id}/contract")
        assert by_id.status_code == by_booking.status_code == 200
        assert by_id.json() == by_booking.json() == contract

        buyer_accept = client.post(
            f"/v1/contracts/{contract_id}/accept/buyer",
            headers={"Idempotency-Key": "pr12-buyer-accept-da"},
        )
        assert buyer_accept.status_code == 200
        buyer_state = buyer_accept.json()
        assert buyer_state["status"] == "partially_accepted"
        assert buyer_state["version"] == 2
        assert buyer_state["buyer_signed_at"] is not None
        assert buyer_state["operator_signed_at"] is None
        assert buyer_state["accepted_at"] is None

        buyer_replay = client.post(
            f"/v1/contracts/{contract_id}/accept/buyer",
            headers={"Idempotency-Key": "pr12-buyer-accept-da"},
        )
        assert buyer_replay.status_code == 200
        assert buyer_replay.json() == buyer_state

        duplicate_buyer = client.post(
            f"/v1/contracts/{contract_id}/accept/buyer",
            headers={"Idempotency-Key": "pr12-buyer-accept-da-2"},
        )
        assert duplicate_buyer.status_code == 409

        operator_accept = client.post(
            f"/v1/contracts/{contract_id}/accept/operator",
            headers={"Idempotency-Key": "pr12-operator-accept-da"},
        )
        assert operator_accept.status_code == 200
        accepted = operator_accept.json()
        assert accepted["status"] == "accepted"
        assert accepted["version"] == 4
        assert accepted["operator_signed_at"] is not None
        assert accepted["accepted_at"] is not None

        booking = client.get(f"/v1/bookings/{booking_id}")
        mission = client.get(f"/v1/missions/{mission_id}")
        assert booking.status_code == mission.status_code == 200
        assert booking.json()["state"] == "pending_contract"
        assert mission.json()["status"] == "selected"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(contract_id)},
                )
                .scalars()
                .all()
            )
            assert events == [
                "CONTRACT_CREATED",
                "CONTRACT_BUYER_ACCEPTED",
                "CONTRACT_OPERATOR_ACCEPTED",
                "CONTRACT_ACCEPTED",
            ]
            persisted = connection.execute(
                text(
                    "SELECT document_version, status, buyer_signed_at, "
                    "operator_signed_at, accepted_at FROM contracts WHERE id = :id"
                ),
                {"id": UUID(contract_id)},
            ).one()
            assert persisted.document_version == 1
            assert persisted.status == "accepted"
            assert persisted.buyer_signed_at is not None
            assert persisted.operator_signed_at is not None
            assert persisted.accepted_at is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_contract_api_rejects_unknown_resources_and_invalid_metadata() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        unknown_booking = client.post(
            f"/v1/bookings/{UUID(int=91201)}/contract",
            headers={"Idempotency-Key": "pr12-unknown-booking"},
            json=_contract_body(),
        )
        assert unknown_booking.status_code == 404

        unknown_contract = client.post(
            f"/v1/contracts/{UUID(int=91202)}/accept/buyer",
            headers={"Idempotency-Key": "pr12-unknown-contract"},
        )
        assert unknown_contract.status_code == 404

        booking_id, _, _, _ = _setup_booking(client, suffix="DB")
        invalid_metadata = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-invalid-metadata"},
            json={
                "document_reference": "contract.pdf",
                "document_version": 1,
                "metadata": {"Template": "A", " template ": "B"},
            },
        )
        assert invalid_metadata.status_code == 422

        missing = client.get(f"/v1/bookings/{booking_id}/contract")
        assert missing.status_code == 404


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_buyer_and_operator_acceptance_both_commit_once() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="DC")
        created = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-dc"},
            json=_contract_body(),
        )
        assert created.status_code == 201
        contract_id = str(created.json()["id"])
        barrier = Barrier(2)

        def accept(party: str, key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/contracts/{contract_id}/accept/{party}",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            buyer = executor.submit(accept, "buyer", "pr12-race-buyer-dc")
            operator = executor.submit(accept, "operator", "pr12-race-operator-dc")
            statuses = sorted((buyer.result(), operator.result()))

        assert statuses == [200, 200]
        final = client.get(f"/v1/contracts/{contract_id}")
        assert final.status_code == 200
        assert final.json()["status"] == "accepted"
        assert final.json()["buyer_signed_at"] is not None
        assert final.json()["operator_signed_at"] is not None

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(contract_id)},
                )
                .scalars()
                .all()
            )
            assert events.count("CONTRACT_BUYER_ACCEPTED") == 1
            assert events.count("CONTRACT_OPERATOR_ACCEPTED") == 1
            assert events.count("CONTRACT_ACCEPTED") == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_same_party_acceptance_is_single_use() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="DD")
        created = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr12-contract-dd"},
            json=_contract_body(),
        )
        assert created.status_code == 201
        contract_id = str(created.json()["id"])
        barrier = Barrier(2)

        def accept(key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/contracts/{contract_id}/accept/buyer",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(accept, "pr12-race-same-dd-1")
            second = executor.submit(accept, "pr12-race-same-dd-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [200, 409]
        final = client.get(f"/v1/contracts/{contract_id}").json()
        assert final["status"] == "partially_accepted"
        assert final["buyer_signed_at"] is not None
        assert final["operator_signed_at"] is None

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            buyer_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE aggregate_id = :id AND event_type = 'CONTRACT_BUYER_ACCEPTED'"
                ),
                {"id": UUID(contract_id)},
            ).scalar_one()
            assert buyer_events == 1
    finally:
        engine.dispose()
