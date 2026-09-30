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


def _organization(client: TestClient, *, suffix: str, kind: str) -> str:
    response = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr23-org-{suffix}"},
        json={
            "type": kind,
            "legal_name": f"PR23 {kind.title()} {suffix}",
            "country": "GR",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _airport(
    client: TestClient,
    *,
    icao: str,
    iata: str | None,
    lat: str,
    lon: str,
) -> str:
    response = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr23-airport-{icao}"},
        json={
            "icao": icao,
            "iata": iata,
            "lat": lat,
            "lon": lon,
            "timezone": "Europe/Athens",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _operator(
    client: TestClient,
    *,
    suffix: str,
) -> str:
    organization_id = _organization(client, suffix=f"OP-{suffix}", kind="operator")
    response = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr23-operator-{suffix}"},
        json={
            "organization_id": organization_id,
            "aoc_reference": f"GR-PR23-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _aircraft(
    client: TestClient,
    *,
    operator_id: str,
    home_base: str,
    suffix: str,
    ordinal: int,
) -> str:
    response = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr23-aircraft-{suffix}-{ordinal}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-{suffix}{ordinal}",
            "aircraft_type": {
                "manufacturer": "PR23 Airframes",
                "model": f"RecoveryJet {suffix}-{ordinal}",
                "category": "regional",
                "seats_min": 10,
                "seats_max": 100,
                "range_nm": 3000,
                "runway_requirements": {},
                "baggage_cargo_profile": {},
            },
            "seat_capacity": 72,
            "cargo_capacity": "1000",
            "range_nm": 2800,
            "home_base": home_base,
            "status": "active",
        },
    )
    assert response.status_code == 201
    seed_capacity_reference_profile(
        response.json(),
        source=f"pr23-capacity-{suffix}-{ordinal}",
    )
    return str(response.json()["id"])


def _make_available(
    client: TestClient,
    *,
    aircraft_id: str,
    departure: datetime,
    suffix: str,
) -> None:
    response = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": f"pr23-availability-{suffix}"},
        json={
            "valid_from": (departure - timedelta(hours=3)).isoformat(),
            "valid_to": (departure + timedelta(hours=6)).isoformat(),
            "status": "available",
            "source": "pr23-operations",
            "provenance": {"fixture": "pr23"},
        },
    )
    assert response.status_code == 201


def _booked_operation(
    client: TestClient,
    *,
    suffix: str,
) -> dict[str, str | datetime]:
    buyer_id = _organization(client, suffix=f"BUY-{suffix}", kind="buyer")
    other_buyer_id = _organization(client, suffix=f"BUY-X-{suffix}", kind="buyer")
    origin = _airport(
        client,
        icao=f"Q{suffix}A",
        iata=None,
        lat="37.9364",
        lon="23.9445",
    )
    destination = _airport(
        client,
        icao=f"Q{suffix}B",
        iata=None,
        lat="40.5197",
        lon="22.9709",
    )
    operator_id = _operator(client, suffix=f"{suffix}A")
    original_aircraft_id = _aircraft(
        client,
        operator_id=operator_id,
        home_base=origin,
        suffix=f"{suffix}A",
        ordinal=1,
    )
    replacement_aircraft_id = _aircraft(
        client,
        operator_id=operator_id,
        home_base=origin,
        suffix=f"{suffix}A",
        ordinal=2,
    )
    other_operator_id = _operator(client, suffix=f"{suffix}B")
    other_aircraft_id = _aircraft(
        client,
        operator_id=other_operator_id,
        home_base=origin,
        suffix=f"{suffix}B",
        ordinal=1,
    )

    departure = datetime.now(UTC) + timedelta(days=7)
    replacement_position = client.post(
        f"/v1/aircraft/{replacement_aircraft_id}/positions",
        headers={"Idempotency-Key": f"pr23-position-{suffix}-replacement"},
        json={
            "airport_id": origin,
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr23-operations",
            "provenance": {"fixture": "pr23", "role": "replacement"},
        },
    )
    assert replacement_position.status_code == 201
    _make_available(
        client,
        aircraft_id=replacement_aircraft_id,
        departure=departure,
        suffix=f"{suffix}-replacement",
    )

    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr23-mission-{suffix}"},
        json={
            "buyer_id": buyer_id,
            "origin_airport_id": origin,
            "destination_airport_id": destination,
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 40,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr23-open-{suffix}"},
    )
    assert opened.status_code == 200

    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr23-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    rfq_id = str(rfq.json()["id"])
    acknowledged = client.post(
        f"/v1/rfqs/{rfq_id}/acknowledge",
        headers={"Idempotency-Key": f"pr23-ack-{suffix}"},
    )
    assert acknowledged.status_code == 200

    quote = client.post(
        f"/v1/rfqs/{rfq_id}/quotes",
        headers={"Idempotency-Key": f"pr23-quote-{suffix}"},
        json={
            "aircraft_id": original_aircraft_id,
            "currency": "EUR",
            "base_amount_minor": 8_000_000,
            "repositioning_amount_minor": 100_000,
            "price_components": [
                {
                    "category": "handling",
                    "label": "Handling",
                    "amount_minor": 50_000,
                    "applicability": "known",
                },
                {
                    "category": "deicing",
                    "label": "Conditional deicing",
                    "amount_minor": 30_000,
                    "applicability": "conditional",
                    "condition": "Required if icing conditions occur",
                },
            ],
            "inclusions": ["standard catering"],
            "exclusions": [],
            "valid_until": (departure - timedelta(days=1)).isoformat(),
        },
    )
    assert quote.status_code == 201
    quote_id = str(quote.json()["id"])

    award = client.post(
        f"/v1/quotes/{quote_id}/accept",
        headers={"Idempotency-Key": f"pr23-award-{suffix}"},
    )
    assert award.status_code == 201
    booking_id = str(award.json()["id"])

    return {
        "buyer_id": buyer_id,
        "other_buyer_id": other_buyer_id,
        "operator_id": operator_id,
        "other_operator_id": other_operator_id,
        "original_aircraft_id": original_aircraft_id,
        "replacement_aircraft_id": replacement_aircraft_id,
        "other_aircraft_id": other_aircraft_id,
        "mission_id": mission_id,
        "quote_id": quote_id,
        "booking_id": booking_id,
        "origin_id": origin,
        "destination_id": destination,
        "departure": departure,
    }


@pytest.mark.integration
def test_pr23_disruption_workflow_preserves_booking_quote_and_party_authority() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="DA")
        booking_id = str(setup["booking_id"])
        buyer_id = str(setup["buyer_id"])
        other_buyer_id = str(setup["other_buyer_id"])
        operator_id = str(setup["operator_id"])
        other_operator_id = str(setup["other_operator_id"])
        other_aircraft_id = str(setup["other_aircraft_id"])
        original_aircraft_id = str(setup["original_aircraft_id"])
        replacement_aircraft_id = str(setup["replacement_aircraft_id"])
        quote_id = str(setup["quote_id"])

        created = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-disruption-da",
            },
            json={
                "disruption_type": "technical",
                "reason": "Hydraulic inspection requires aircraft substitution",
            },
        )
        assert created.status_code == 201
        disruption = created.json()
        disruption_id = str(disruption["id"])
        assert disruption["status"] == "open"

        replay = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-disruption-da",
            },
            json={
                "disruption_type": "technical",
                "reason": "Hydraulic inspection requires aircraft substitution",
            },
        )
        assert replay.status_code == 201
        assert replay.json() == disruption

        assert (
            client.get(
                f"/v1/disruptions/{disruption_id}",
                headers={"X-Buyer-Id": other_buyer_id},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/disruptions/{disruption_id}",
                headers={"X-Operator-Id": other_operator_id},
            ).status_code
            == 404
        )
        owner_view = client.get(
            f"/v1/disruptions/{disruption_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert owner_view.status_code == 200

        hidden_award = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-cross-operator-da",
            },
            json={
                "proposed_operator_id": other_operator_id,
                "proposed_aircraft_id": other_aircraft_id,
                "source": "dispatch",
                "source_evidence": "External lift requested",
            },
        )
        assert hidden_award.status_code == 409

        empty_after_failure = client.get(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={"X-Operator-Id": operator_id},
        )
        assert empty_after_failure.status_code == 200
        assert empty_after_failure.json()["returned_count"] == 0

        proposal = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-proposal-da",
            },
            json={
                "proposed_aircraft_id": replacement_aircraft_id,
                "source": "dispatch",
                "source_evidence": "Current fleet availability checked at proposal time",
            },
        )
        assert proposal.status_code == 201
        proposal_id = str(proposal.json()["id"])
        assert proposal.json()["requires_buyer_decision"] is True
        assert proposal.json()["proposed_operator_id"] == operator_id
        assert proposal.json()["proposed_aircraft_id"] == replacement_aircraft_id
        assert proposal.json()["proposed_operator_version"] >= 1
        assert proposal.json()["proposed_aircraft_version"] >= 1
        assert proposal.json()["availability_record_id"] is not None
        assert proposal.json()["availability_recorded_at"] is not None

        fx_attempt = client.post(
            f"/v1/disruptions/{disruption_id}/requotes",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-fx-da",
            },
            json={
                "proposal_id": proposal_id,
                "currency": "USD",
                "known_adjustment_minor": 100_000,
                "terms_summary": "Must fail because accepted quote is EUR",
            },
        )
        assert fx_attempt.status_code == 409

        first_change = client.post(
            f"/v1/disruptions/{disruption_id}/requotes",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-change-1-da",
            },
            json={
                "proposal_id": proposal_id,
                "currency": "EUR",
                "known_adjustment_minor": 100_000,
                "conditional_adjustment_minor": 50_000,
                "terms_summary": "Replacement handling plus conditional positioning",
            },
        )
        assert first_change.status_code == 201
        first_change_id = str(first_change.json()["id"])
        assert first_change.json()["original_quote_id"] == quote_id

        second_change = client.post(
            f"/v1/disruptions/{disruption_id}/requotes",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-change-2-da",
            },
            json={
                "proposal_id": proposal_id,
                "currency": "EUR",
                "known_adjustment_minor": 125_000,
                "conditional_adjustment_minor": 25_000,
                "terms_summary": "Revised replacement handling evidence",
            },
        )
        assert second_change.status_code == 201
        second_change_id = str(second_change.json()["id"])
        assert second_change.json()["supersedes_change_id"] == first_change_id

        stale_decision = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr23-stale-decision-da",
            },
            json={
                "proposal_id": proposal_id,
                "commercial_change_id": first_change_id,
                "decision": "approved",
            },
        )
        assert stale_decision.status_code == 409

        approval = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr23-decision-da",
            },
            json={
                "proposal_id": proposal_id,
                "commercial_change_id": second_change_id,
                "decision": "approved",
                "note": "Approved exact current replacement and commercial revision",
            },
        )
        assert approval.status_code == 201
        assert approval.json()["decision"] == "approved"

        resolved = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-resolve-da",
            },
            json={
                "proposal_id": proposal_id,
                "outcome": "Replacement aircraft accepted; operation restored",
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["selected_proposal_id"] == proposal_id
        assert resolved.json()["selected_commercial_change_id"] == second_change_id
        assert resolved.json()["selected_buyer_decision_id"] == approval.json()["id"]

        resolution_replay = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-resolve-da",
            },
            json={
                "proposal_id": proposal_id,
                "outcome": "Replacement aircraft accepted; operation restored",
            },
        )
        assert resolution_replay.status_code == 200
        assert resolution_replay.json() == resolved.json()

        terminal_mutation = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-after-resolve-da",
            },
            json={
                "proposed_aircraft_id": original_aircraft_id,
                "source": "dispatch",
            },
        )
        assert terminal_mutation.status_code == 409

        booking = client.get(f"/v1/bookings/{booking_id}")
        assert booking.status_code == 200
        assert booking.json()["accepted_quote_id"] == quote_id
        assert booking.json()["operator_id"] == operator_id
        assert booking.json()["aircraft_id"] == original_aircraft_id

        representations = [
            created.json(),
            owner_view.json(),
            proposal.json(),
            first_change.json(),
            approval.json(),
            resolved.json(),
        ]
        for representation in representations:
            serialized = str(representation).lower()
            assert "losing_bid" not in serialized
            assert "tender_invitation" not in serialized
            assert "competitor" not in serialized

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            event_types: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(disruption_id)},
                )
                .scalars()
                .all()
            )
            assert event_types == [
                "DISRUPTION_OPENED",
                "DISRUPTION_PROPOSAL_CREATED",
                "DISRUPTION_COMMERCIAL_CHANGE_CREATED",
                "DISRUPTION_COMMERCIAL_CHANGE_SUPERSEDED",
                "DISRUPTION_COMMERCIAL_CHANGE_CREATED",
                "DISRUPTION_BUYER_APPROVED",
                "DISRUPTION_RESOLVED",
            ]
            assert (
                connection.execute(
                    text("SELECT count(*) FROM disruption_proposals WHERE disruption_id = :id"),
                    {"id": UUID(disruption_id)},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM disruption_commercial_changes "
                        "WHERE disruption_id = :id"
                    ),
                    {"id": UUID(disruption_id)},
                ).scalar_one()
                == 2
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr23_buyer_rejection_requires_new_evidence_before_resolution() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="DB")
        booking_id = str(setup["booking_id"])
        buyer_id = str(setup["buyer_id"])
        operator_id = str(setup["operator_id"])
        departure = setup["departure"]
        assert isinstance(departure, datetime)

        disruption = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-disruption-db",
            },
            json={"disruption_type": "delay", "reason": "Crew arrival delay"},
        )
        assert disruption.status_code == 201
        disruption_id = str(disruption.json()["id"])

        proposal = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-proposal-db",
            },
            json={
                "departure_window": {
                    "start": (departure + timedelta(hours=1)).isoformat(),
                    "end": (departure + timedelta(hours=3)).isoformat(),
                },
                "source": "operations",
                "source_evidence": "Crew duty recovery plan",
            },
        )
        assert proposal.status_code == 201
        proposal_id = str(proposal.json()["id"])
        assert proposal.json()["requires_buyer_decision"] is True

        rejected = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr23-reject-db",
            },
            json={
                "proposal_id": proposal_id,
                "decision": "rejected",
                "note": "Delay is not acceptable",
            },
        )
        assert rejected.status_code == 201
        assert rejected.json()["decision"] == "rejected"

        blocked = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-blocked-resolution-db",
            },
            json={"proposal_id": proposal_id, "outcome": "Must not resolve"},
        )
        assert blocked.status_code == 409

        replacement = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-replacement-db",
            },
            json={
                "source": "operations",
                "source_evidence": "Original aircraft and schedule restored",
            },
        )
        assert replacement.status_code == 201
        assert replacement.json()["supersedes_proposal_id"] == proposal_id
        assert replacement.json()["requires_buyer_decision"] is False

        resolved = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-resolve-db",
            },
            json={
                "proposal_id": replacement.json()["id"],
                "outcome": "Original operation restored with no buyer-facing change",
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr23_competing_buyer_decisions_and_terminal_resolutions_serialize() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="DC")
        booking_id = str(setup["booking_id"])
        buyer_id = str(setup["buyer_id"])
        operator_id = str(setup["operator_id"])
        replacement_aircraft_id = str(setup["replacement_aircraft_id"])

        decision_disruption = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-race-disruption-dc",
            },
            json={"disruption_type": "aircraft_unavailable", "reason": "Aircraft unavailable"},
        )
        assert decision_disruption.status_code == 201
        decision_disruption_id = str(decision_disruption.json()["id"])
        material = client.post(
            f"/v1/disruptions/{decision_disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-race-proposal-dc",
            },
            json={
                "proposed_aircraft_id": replacement_aircraft_id,
                "source": "dispatch",
            },
        )
        assert material.status_code == 201
        material_id = str(material.json()["id"])
        barrier = Barrier(2)

        def decide(value: str, key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/disruptions/{decision_disruption_id}/buyer-decisions",
                headers={"X-Buyer-Id": buyer_id, "Idempotency-Key": key},
                json={"proposal_id": material_id, "decision": value},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            approve = executor.submit(decide, "approved", "pr23-race-approve-dc")
            reject = executor.submit(decide, "rejected", "pr23-race-reject-dc")
            decision_statuses = sorted((approve.result(), reject.result()))
        assert decision_statuses == [201, 409]

        resolution_disruption = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-resolve-race-disruption-dc",
            },
            json={"disruption_type": "other", "reason": "Operational coordination issue"},
        )
        assert resolution_disruption.status_code == 201
        resolution_disruption_id = str(resolution_disruption.json()["id"])
        nonmaterial = client.post(
            f"/v1/disruptions/{resolution_disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr23-resolve-race-proposal-dc",
            },
            json={"source": "operations", "source_evidence": "No material term changed"},
        )
        assert nonmaterial.status_code == 201
        nonmaterial_id = str(nonmaterial.json()["id"])
        resolve_barrier = Barrier(2)

        def resolve(key: str) -> int:
            resolve_barrier.wait()
            response = client.post(
                f"/v1/disruptions/{resolution_disruption_id}/resolve",
                headers={"X-Operator-Id": operator_id, "Idempotency-Key": key},
                json={"proposal_id": nonmaterial_id, "outcome": "Operation restored"},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(resolve, "pr23-race-resolve-1-dc")
            second = executor.submit(resolve, "pr23-race-resolve-2-dc")
            resolution_statuses = sorted((first.result(), second.result()))
        assert resolution_statuses == [200, 409]
