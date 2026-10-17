import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.main import create_app
from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.shared.config import Settings


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _organization(client: TestClient, *, suffix: str, kind: str) -> str:
    response = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr22-org-{suffix}"},
        json={
            "type": kind,
            "legal_name": f"PR22 {kind.title()} {suffix}",
            "country": "GR",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _airport(
    client: TestClient,
    *,
    icao: str,
    iata: str,
    lat: str,
    lon: str,
) -> str:
    response = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr22-airport-{icao}"},
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


def _operator_and_aircraft(
    client: TestClient,
    *,
    home_base: str,
) -> tuple[str, str, UUID]:
    organization_id = _organization(client, suffix="OP-A", kind="operator")
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": "pr22-operator-a"},
        json={
            "organization_id": organization_id,
            "aoc_reference": "GR-PR22-A",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": "pr22-aircraft-a"},
        json={
            "operator_id": operator.json()["id"],
            "registration": "SX-P22A",
            "aircraft_type": {
                "manufacturer": "PR22 Airframes",
                "model": "BuyerJet A",
                "category": "regional",
                "seats_min": 20,
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
    assert aircraft.status_code == 201
    return (
        str(operator.json()["id"]),
        str(aircraft.json()["id"]),
        UUID(aircraft.json()["aircraft_type_id"]),
    )


def _insert_profile(
    settings: Settings,
    aircraft_type_id: UUID,
) -> None:
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                MatchingReferenceProfileRow(
                    id=uuid4(),
                    aircraft_type_id=aircraft_type_id,
                    cruise_speed_kts=430,
                    operating_cost_per_hour_minor=500_000,
                    operating_cost_currency="EUR",
                    max_reposition_nm=1_000,
                    turnaround_buffer_minutes=45,
                    source="pr22-test-reference",
                    provenance={"fixture": "pr22"},
                    recorded_at=datetime.now(UTC) - timedelta(minutes=1),
                    superseded_at=None,
                )
            )
    finally:
        engine.dispose()


def _record_operational_state(
    client: TestClient,
    *,
    aircraft_id: str,
    airport_id: str,
    departure: datetime,
) -> None:
    position = client.post(
        f"/v1/aircraft/{aircraft_id}/positions",
        headers={"Idempotency-Key": "pr22-position-a"},
        json={
            "airport_id": airport_id,
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr22-test",
            "provenance": {"fixture": "pr22"},
        },
    )
    assert position.status_code == 201
    availability = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": "pr22-availability-a"},
        json={
            "valid_from": (departure - timedelta(hours=2)).isoformat(),
            "valid_to": (departure + timedelta(hours=4)).isoformat(),
            "status": "available",
            "source": "pr22-test",
            "provenance": {"fixture": "pr22"},
        },
    )
    assert availability.status_code == 201


def _quote_body(
    *,
    aircraft_id: str,
    amount_minor: int,
    valid_until: datetime,
) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount_minor,
        "repositioning_amount_minor": 100_000,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 50_000,
                "applicability": "known",
            }
        ],
        "inclusions": ["catering"],
        "exclusions": [],
        "cancellation_terms": "Standard cancellation terms",
        "payment_terms": "Net 7",
        "valid_until": valid_until.isoformat(),
    }


@pytest.mark.integration
def test_pr22_buyer_procurement_composes_existing_authorities_and_fails_closed() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_a = _organization(client, suffix="BUY-A", kind="buyer")
        buyer_b = _organization(client, suffix="BUY-B", kind="buyer")
        origin = _airport(
            client,
            icao="ZPAA",
            iata="ZPA",
            lat="37.9364",
            lon="23.9445",
        )
        destination = _airport(
            client,
            icao="ZPBQ",
            iata="ZPB",
            lat="40.5197",
            lon="22.9709",
        )
        operator_id, aircraft_id, aircraft_type_id = _operator_and_aircraft(
            client,
            home_base=origin,
        )
        _insert_profile(settings, aircraft_type_id)

        departure = datetime.now(UTC) + timedelta(days=10)
        _record_operational_state(
            client,
            aircraft_id=aircraft_id,
            airport_id=origin,
            departure=departure,
        )

        mission_body = {
            "origin_airport_id": origin,
            "destination_airport_id": destination,
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 40,
            "max_budget": {
                "amount_minor": 15_000_000,
                "currency": "EUR",
            },
            "special_requirements": ["wifi"],
        }
        mission = client.post(
            "/v1/buyer-portal/missions",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-mission-a",
            },
            json=mission_body,
        )
        assert mission.status_code == 201
        mission_id = str(mission.json()["id"])
        replay = client.post(
            "/v1/buyer-portal/missions",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-mission-a",
            },
            json=mission_body,
        )
        assert replay.status_code == 201
        assert replay.json() == mission.json()

        cross_mission = client.get(
            f"/v1/buyer-portal/missions/{mission_id}",
            headers={"X-Buyer-Id": buyer_b},
        )
        assert cross_mission.status_code == 404

        opened = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/open",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-open-a",
            },
        )
        assert opened.status_code == 200
        assert opened.json()["status"] == "open"

        suppliers = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/suppliers",
            headers={"X-Buyer-Id": buyer_a},
        )
        assert suppliers.status_code == 200
        assert suppliers.json()["returned_count"] == 1
        assert suppliers.json()["suppliers"][0]["operator_id"] == operator_id
        assert suppliers.json()["suppliers"][0]["best_aircraft_id"] == aircraft_id
        assert suppliers.json()["suppliers"][0]["matching_rank"] == 1

        rfqs = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/rfqs",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-rfq-batch-a",
            },
            json={
                "operator_ids": [operator_id],
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert rfqs.status_code == 201
        assert rfqs.json()["issued_count"] == 1
        rfq_id = str(rfqs.json()["rfqs"][0]["id"])

        cross_rfqs = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/rfqs",
            headers={"X-Buyer-Id": buyer_b},
        )
        assert cross_rfqs.status_code == 404

        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr22-rfq-ack-a"},
        )
        assert acknowledged.status_code == 200

        quote = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr22-quote-a"},
            json=_quote_body(
                aircraft_id=aircraft_id,
                amount_minor=4_000_000,
                valid_until=departure - timedelta(days=1),
            ),
        )
        assert quote.status_code == 201
        quote_id = str(quote.json()["id"])

        comparison = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/compare",
            headers={"X-Buyer-Id": buyer_a},
        )
        assert comparison.status_code == 200
        assert comparison.json()["global_rank_available"] is True
        assert comparison.json()["quotes"][0]["quote_id"] == quote_id
        assert comparison.json()["quotes"][0]["decision_eligible"] is True
        assert comparison.json()["quotes"][0]["currency"] == "EUR"

        approval = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/{quote_id}/approve",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-approval-a",
            },
            json={"note": "Approved after commercial review"},
        )
        assert approval.status_code == 201
        first_approval_id = str(approval.json()["id"])
        assert approval.json()["quote_id"] == quote_id
        assert approval.json()["status"] == "approved"

        revised = client.post(
            f"/v1/quotes/{quote_id}/revise",
            headers={"Idempotency-Key": "pr22-revise-a"},
            json=_quote_body(
                aircraft_id=aircraft_id,
                amount_minor=3_900_000,
                valid_until=departure - timedelta(hours=12),
            ),
        )
        assert revised.status_code == 201
        revised_quote_id = str(revised.json()["id"])

        stale_award = client.post(
            f"/v1/buyer-portal/approvals/{first_approval_id}/award",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-stale-award-a",
            },
        )
        assert stale_award.status_code == 409

        refreshed_comparison = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/compare",
            headers={"X-Buyer-Id": buyer_a},
        )
        assert refreshed_comparison.status_code == 200
        assert [item["quote_id"] for item in refreshed_comparison.json()["quotes"]] == [
            revised_quote_id
        ]

        replacement_approval = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/{revised_quote_id}/approve",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-approval-b",
            },
            json={"note": "Approved revised commercial terms"},
        )
        assert replacement_approval.status_code == 201
        approval_id = str(replacement_approval.json()["id"])
        assert replacement_approval.json()["supersedes_approval_id"] == first_approval_id

        award = client.post(
            f"/v1/buyer-portal/approvals/{approval_id}/award",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-award-a",
            },
        )
        assert award.status_code == 201
        assert award.json()["approval"]["status"] == "consumed"
        booking_id = str(award.json()["booking"]["id"])
        assert award.json()["approval"]["booking_id"] == booking_id
        assert award.json()["booking"]["accepted_quote_id"] == revised_quote_id
        assert award.json()["booking"]["state"] == "pending_contract"

        award_replay = client.post(
            f"/v1/buyer-portal/approvals/{approval_id}/award",
            headers={
                "X-Buyer-Id": buyer_a,
                "Idempotency-Key": "pr22-award-a",
            },
        )
        assert award_replay.status_code == 201
        assert award_replay.json() == award.json()

        booking = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/booking",
            headers={"X-Buyer-Id": buyer_a},
        )
        assert booking.status_code == 200
        assert booking.json()["id"] == booking_id

        cross_booking = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/booking",
            headers={"X-Buyer-Id": buyer_b},
        )
        assert cross_booking.status_code == 404

        audit = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/audit",
            headers={"X-Buyer-Id": buyer_a},
        )
        assert audit.status_code == 200
        events = audit.json()["events"]
        event_types = {item["event_type"] for item in events}
        aggregate_types = {item["aggregate_type"] for item in events}
        assert "MISSION_CREATED" in event_types
        assert "RFQ_SENT" in event_types
        assert "QUOTE_REVISED" in event_types
        assert "PROCUREMENT_QUOTE_APPROVED" in event_types
        assert "PROCUREMENT_APPROVAL_SUPERSEDED" in event_types
        assert "PROCUREMENT_APPROVAL_CONSUMED" in event_types
        assert "BOOKING_CREATED" in event_types
        assert "procurement_approval" in aggregate_types
        assert "booking" in aggregate_types
        assert all("canonical_json" not in item for item in events)
        assert all("payload" not in item for item in events)

        cross_audit = client.get(
            f"/v1/buyer-portal/missions/{mission_id}/audit",
            headers={"X-Buyer-Id": buyer_b},
        )
        assert cross_audit.status_code == 404
