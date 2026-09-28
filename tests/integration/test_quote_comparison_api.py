from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.main import create_app
from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.shared.config import Settings
from tests.integration.matching_support import settings


def _create_operator_and_aircraft(
    client: TestClient,
    *,
    suffix: str,
    home_base_id: str,
) -> tuple[str, str, UUID]:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr10-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR10 Operator {suffix}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr10-operator-{suffix}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR10-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr10-aircraft-{suffix}"},
        json={
            "operator_id": operator.json()["id"],
            "registration": f"SX-C{suffix}",
            "aircraft_type": {
                "manufacturer": "PR10 Airframes",
                "model": f"DecisionJet {suffix}",
                "category": "regional",
                "seats_min": 40,
                "seats_max": 120,
                "range_nm": 3000,
            },
            "seat_capacity": 90,
            "cargo_capacity": "1200",
            "range_nm": 2800,
            "home_base": home_base_id,
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
    settings_value: Settings,
    aircraft_type_id: UUID,
    *,
    recorded_at: datetime,
    suffix: str,
) -> None:
    engine = create_engine(settings_value.database_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                MatchingReferenceProfileRow(
                    id=uuid4(),
                    aircraft_type_id=aircraft_type_id,
                    cruise_speed_kts=450,
                    operating_cost_per_hour_minor=600_000,
                    operating_cost_currency="EUR",
                    max_reposition_nm=1_000,
                    turnaround_buffer_minutes=45,
                    source="pr10-comparison-test",
                    provenance={"fixture": suffix},
                    recorded_at=recorded_at,
                    superseded_at=None,
                )
            )
    finally:
        engine.dispose()


def _record_operational_state(
    client: TestClient,
    *,
    aircraft_id: str,
    suffix: str,
    departure: datetime,
    airport_id: str | None,
    lat: str | None = None,
    lon: str | None = None,
) -> None:
    position_body: dict[str, object] = {
        "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "source": "pr10-comparison-test",
        "provenance": {"fixture": suffix},
    }
    if airport_id is not None:
        position_body["airport_id"] = airport_id
    else:
        position_body["lat"] = lat
        position_body["lon"] = lon
    position = client.post(
        f"/v1/aircraft/{aircraft_id}/positions",
        headers={"Idempotency-Key": f"pr10-position-{suffix}"},
        json=position_body,
    )
    assert position.status_code == 201
    availability = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": f"pr10-availability-{suffix}"},
        json={
            "valid_from": (departure - timedelta(hours=1)).isoformat(),
            "valid_to": (departure + timedelta(hours=3)).isoformat(),
            "status": "available",
            "source": "pr10-comparison-test",
            "provenance": {"fixture": suffix},
        },
    )
    assert availability.status_code == 201


def _create_rfq_and_quote(
    client: TestClient,
    *,
    mission_id: str,
    operator_id: str,
    aircraft_id: str,
    suffix: str,
    departure: datetime,
    base_amount_minor: int,
    conditional: bool,
) -> str:
    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr10-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    rfq_id = str(rfq.json()["id"])
    acknowledge = client.post(
        f"/v1/rfqs/{rfq_id}/acknowledge",
        headers={"Idempotency-Key": f"pr10-ack-{suffix}"},
    )
    assert acknowledge.status_code == 200

    components: list[dict[str, object]] = [
        {
            "category": "handling",
            "label": "Handling",
            "amount_minor": 100_000,
            "applicability": "known",
        }
    ]
    if conditional:
        components.append(
            {
                "category": "deicing",
                "label": "Deicing",
                "amount_minor": 500_000,
                "applicability": "conditional",
                "condition": "Only if required before departure",
            }
        )

    quote = client.post(
        f"/v1/rfqs/{rfq_id}/quotes",
        headers={"Idempotency-Key": f"pr10-quote-{suffix}"},
        json={
            "aircraft_id": aircraft_id,
            "currency": "EUR",
            "base_amount_minor": base_amount_minor,
            "repositioning_amount_minor": 200_000,
            "price_components": components,
            "inclusions": ["Catering", "WiFi"],
            "exclusions": [],
            "cancellation_terms": f"Cancellation policy {suffix}",
            "payment_terms": f"Payment terms {suffix}",
            "valid_until": (departure - timedelta(days=1)).isoformat(),
        },
    )
    assert quote.status_code == 201
    return str(quote.json()["id"])


@pytest.mark.integration
def test_mission_quote_comparison_is_explainable_and_operationally_grounded() -> None:
    settings_value = settings()
    with TestClient(create_app(settings_value)) as client:
        origin = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "pr10-origin"},
            json={
                "icao": "PTAA",
                "iata": "PQA",
                "lat": "37.9364",
                "lon": "23.9445",
                "timezone": "Europe/Athens",
            },
        )
        destination = client.post(
            "/v1/airports",
            headers={"Idempotency-Key": "pr10-destination"},
            json={
                "icao": "PTAB",
                "iata": "PQB",
                "lat": "40.5197",
                "lon": "22.9709",
                "timezone": "Europe/Athens",
            },
        )
        assert origin.status_code == destination.status_code == 201

        buyer = client.post(
            "/v1/organizations",
            headers={"Idempotency-Key": "pr10-buyer"},
            json={
                "type": "buyer",
                "legal_name": "PR10 Buyer",
                "country": "GR",
            },
        )
        assert buyer.status_code == 201

        operator_a, aircraft_a, type_a = _create_operator_and_aircraft(
            client,
            suffix="A1",
            home_base_id=str(origin.json()["id"]),
        )
        operator_b, aircraft_b, type_b = _create_operator_and_aircraft(
            client,
            suffix="B1",
            home_base_id=str(origin.json()["id"]),
        )

        departure = datetime.now(UTC) + timedelta(days=7)
        mission = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr10-mission"},
            json={
                "buyer_id": buyer.json()["id"],
                "origin_airport_id": origin.json()["id"],
                "destination_airport_id": destination.json()["id"],
                "departure_window": {
                    "start": departure.isoformat(),
                    "end": (departure + timedelta(hours=2)).isoformat(),
                },
                "passenger_count": 70,
                "max_budget": {
                    "amount_minor": 20_000_000,
                    "currency": "EUR",
                },
            },
        )
        assert mission.status_code == 201
        mission_id = str(mission.json()["id"])
        opened = client.post(
            f"/v1/missions/{mission_id}/open",
            headers={"Idempotency-Key": "pr10-open"},
        )
        assert opened.status_code == 200

        now = datetime.now(UTC)
        _insert_profile(
            settings_value,
            type_a,
            recorded_at=now - timedelta(minutes=10),
            suffix="A1",
        )
        _insert_profile(
            settings_value,
            type_b,
            recorded_at=now - timedelta(minutes=10),
            suffix="B1",
        )
        _record_operational_state(
            client,
            aircraft_id=aircraft_a,
            suffix="A1",
            departure=departure,
            airport_id=str(origin.json()["id"]),
        )
        _record_operational_state(
            client,
            aircraft_id=aircraft_b,
            suffix="B1",
            departure=departure,
            airport_id=None,
            lat="40.5197",
            lon="22.9709",
        )

        quote_a = _create_rfq_and_quote(
            client,
            mission_id=mission_id,
            operator_id=operator_a,
            aircraft_id=aircraft_a,
            suffix="A1",
            departure=departure,
            base_amount_minor=7_500_000,
            conditional=False,
        )
        quote_b = _create_rfq_and_quote(
            client,
            mission_id=mission_id,
            operator_id=operator_b,
            aircraft_id=aircraft_b,
            suffix="B1",
            departure=departure,
            base_amount_minor=7_000_000,
            conditional=True,
        )

        response = client.get(f"/v1/missions/{mission_id}/quotes/compare")
        assert response.status_code == 200
        payload = response.json()

        assert payload["comparison_policy_version"] == "quote-comparison-v1"
        assert payload["matching_policy_version"] == "matching-v1"
        assert payload["pricing_currencies"] == ["EUR"]
        assert payload["global_rank_available"] is True
        assert payload["ranking_scope"] == "currency"
        assert payload["free_text_terms_scored"] is False
        assert payload["score_weights"] == {
            "expected_total_points": 2500,
            "worst_case_total_points": 2500,
            "reposition_points": 2000,
            "operational_risk_points": 2000,
            "pricing_confidence_points": 1000,
        }
        assert payload["returned_count"] == 2

        by_id = {item["quote_id"]: item for item in payload["quotes"]}
        assert set(by_id) == {quote_a, quote_b}
        assert {item["currency_rank"] for item in payload["quotes"]} == {1, 2}
        assert all(item["decision_eligible"] is True for item in payload["quotes"])
        assert all(item["aircraft_suitability"]["feasible"] is True for item in payload["quotes"])
        assert by_id[quote_a]["aircraft_suitability"]["reposition_distance_nm"] == "0.0"
        assert by_id[quote_b]["aircraft_suitability"]["reposition_distance_nm"] != "0.0"
        assert by_id[quote_a]["normalization"]["confidence"] == "high"
        assert by_id[quote_b]["normalization"]["confidence"] == "medium"
        assert by_id[quote_a]["cancellation_terms"] == "Cancellation policy A1"
        assert by_id[quote_b]["payment_terms"] == "Payment terms B1"

        for item in payload["quotes"]:
            score = item["score"]
            assert score["method"] == "currency_cohort_minmax_v1"
            assert score["currency_scope"] == "EUR"
            assert score["cohort_size"] == 2
            assert score["total_basis_points"] == sum(
                (
                    score["expected_total_points"],
                    score["worst_case_total_points"],
                    score["reposition_points"],
                    score["operational_risk_points"],
                    score["pricing_confidence_points"],
                )
            )


@pytest.mark.integration
def test_quote_comparison_unknown_mission_and_empty_mission_behavior() -> None:
    settings_value = settings()
    with TestClient(create_app(settings_value)) as client:
        unknown = client.get(f"/v1/missions/{UUID(int=999010)}/quotes/compare")
        assert unknown.status_code == 404
