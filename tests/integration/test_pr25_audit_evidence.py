from __future__ import annotations

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
from tests.integration.test_pr13_booking_workflow_api import _create_accepted_contract
from tests.integration.test_pr17_tenders import _setup_tender
from tests.integration.test_pr23_disruptions import _booked_operation


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _organization(client: TestClient, *, suffix: str, kind: str) -> str:
    response = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr25-org-{suffix}"},
        json={
            "type": kind,
            "legal_name": f"PR25 {kind.title()} {suffix}",
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
        headers={"Idempotency-Key": f"pr25-airport-{icao}"},
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
    suffix: str,
    home_base: str,
) -> tuple[str, str, UUID]:
    organization_id = _organization(client, suffix=f"OP-{suffix}", kind="operator")
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr25-operator-{suffix}"},
        json={
            "organization_id": organization_id,
            "aoc_reference": f"GR-PR25-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr25-aircraft-{suffix}"},
        json={
            "operator_id": operator.json()["id"],
            "registration": f"SX-{suffix}",
            "aircraft_type": {
                "manufacturer": "PR25 Airframes",
                "model": f"EvidenceJet {suffix}",
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
        UUID(str(aircraft.json()["aircraft_type_id"])),
    )


def _insert_profile(settings: Settings, aircraft_type_id: UUID, *, suffix: str) -> None:
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
                    source=f"pr25-{suffix}-reference",
                    provenance={"fixture": "pr25", "suffix": suffix},
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
    suffix: str,
) -> None:
    position = client.post(
        f"/v1/aircraft/{aircraft_id}/positions",
        headers={"Idempotency-Key": f"pr25-position-{suffix}"},
        json={
            "airport_id": airport_id,
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr25-test",
            "provenance": {"fixture": "pr25", "suffix": suffix},
        },
    )
    assert position.status_code == 201
    availability = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": f"pr25-availability-{suffix}"},
        json={
            "valid_from": (departure - timedelta(hours=2)).isoformat(),
            "valid_to": (departure + timedelta(hours=4)).isoformat(),
            "status": "available",
            "source": "pr25-test",
            "provenance": {"fixture": "pr25", "suffix": suffix},
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
def test_pr25_procurement_evidence_is_deterministic_isolated_and_redacted() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id = _organization(client, suffix="BUY-EA", kind="buyer")
        other_buyer_id = _organization(client, suffix="BUY-EB", kind="buyer")
        origin = _airport(
            client,
            icao="E25A",
            iata="E2A",
            lat="37.9364",
            lon="23.9445",
        )
        destination = _airport(
            client,
            icao="E25B",
            iata="E2B",
            lat="40.5197",
            lon="22.9709",
        )
        operator_id, aircraft_id, aircraft_type_id = _operator_and_aircraft(
            client,
            suffix="EVA",
            home_base=origin,
        )
        _insert_profile(settings, aircraft_type_id, suffix="eva")

        departure = datetime.now(UTC) + timedelta(days=10)
        _record_operational_state(
            client,
            aircraft_id=aircraft_id,
            airport_id=origin,
            departure=departure,
            suffix="eva",
        )

        mission = client.post(
            "/v1/buyer-portal/missions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-mission-eva",
            },
            json={
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
            },
        )
        assert mission.status_code == 201
        mission_id = str(mission.json()["id"])

        opened = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/open",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-open-eva",
            },
        )
        assert opened.status_code == 200

        rfqs = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/rfqs",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-rfq-eva",
            },
            json={
                "operator_ids": [operator_id],
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert rfqs.status_code == 201
        rfq_id = str(rfqs.json()["rfqs"][0]["id"])

        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": "pr25-ack-eva"},
        )
        assert acknowledged.status_code == 200

        quote = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr25-quote-eva"},
            json=_quote_body(
                aircraft_id=aircraft_id,
                amount_minor=4_000_000,
                valid_until=departure - timedelta(days=1),
            ),
        )
        assert quote.status_code == 201
        quote_id = str(quote.json()["id"])

        approved = client.post(
            f"/v1/buyer-portal/missions/{mission_id}/quotes/{quote_id}/approve",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-approve-eva",
            },
            json={"note": "Approved against exact comparison evidence"},
        )
        assert approved.status_code == 201
        approval_id = str(approved.json()["id"])

        awarded = client.post(
            f"/v1/buyer-portal/approvals/{approval_id}/award",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-award-eva",
            },
        )
        assert awarded.status_code == 201
        booking_id = str(awarded.json()["booking"]["id"])

        first = client.get(
            f"/v1/evidence/missions/{mission_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert first.status_code == 200
        package = first.json()
        assert package["schema_version"] == "audit-evidence-v1"
        assert package["completeness"] == "complete"
        assert len(package["integrity_digest"]) == 64
        assert "canonical_json" not in first.text
        assert "fx_rate" not in first.text

        decisions = {item["decision_type"]: item for item in package["decisions"]}
        assert set(decisions) == {"supplier_selection", "quote_comparison"}
        supplier = decisions["supplier_selection"]["content"]
        assert supplier["operator_id"] == operator_id
        assert supplier["matching_policy_version"] == "matching-v1"
        assert supplier["match"]["aircraft_id"] == aircraft_id
        assert supplier["match"]["position"]["recorded_at"] is not None
        assert supplier["match"]["availability"]["recorded_at"] is not None

        comparison = decisions["quote_comparison"]["content"]
        assert comparison["selected_quote_id"] == quote_id
        assert comparison["comparison_policy_version"] == "quote-comparison-v1"
        assert comparison["no_implicit_fx"] is True
        selected = next(item for item in comparison["quotes"] if item["quote_id"] == quote_id)
        assert selected["normalization_version"] == "v1"
        assert selected["decision_eligible"] is True

        source_types = {item["source_type"] for item in package["sources"]}
        assert {
            "mission",
            "rfq",
            "quote",
            "procurement_approval",
            "booking",
        }.issubset(source_types)
        quote_source = next(
            item
            for item in package["sources"]
            if item["source_type"] == "quote" and item["source_id"] == quote_id
        )
        assert quote_source["facts"]["price_components"][0]["amount_minor"] == 50_000

        event_types = {item["event_type"] for item in package["events"]}
        assert "PROCUREMENT_QUOTE_APPROVED" in event_types
        assert "PROCUREMENT_APPROVAL_CONSUMED" in event_types
        assert "BOOKING_CREATED" in event_types

        second = client.get(
            f"/v1/evidence/missions/{mission_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert second.status_code == 200
        assert second.json()["integrity_digest"] == package["integrity_digest"]

        assert (
            client.get(
                f"/v1/evidence/missions/{mission_id}",
                headers={"X-Buyer-Id": other_buyer_id},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/evidence/missions/{mission_id}",
                headers={"X-Operator-Id": operator_id},
            ).status_code
            == 404
        )

        operator_booking = client.get(
            f"/v1/evidence/bookings/{booking_id}",
            headers={"X-Operator-Id": operator_id},
        )
        assert operator_booking.status_code == 200
        assert "tender" not in {
            item["source_type"] for item in operator_booking.json()["sources"]
        }
        assert "quote_comparison" not in {
            item["decision_type"] for item in operator_booking.json()["decisions"]
        }
        assert (
            client.get(
                f"/v1/evidence/bookings/{booking_id}",
                headers={"X-Operator-Id": str(uuid4())},
            ).status_code
            == 404
        )

        truncated = client.get(
            f"/v1/evidence/missions/{mission_id}?limit=1",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert truncated.status_code == 200
        assert truncated.json()["completeness"] == "truncated"
        assert truncated.json()["diagnostics"]


@pytest.mark.integration
def test_pr25_active_sealed_tender_evidence_does_not_disclose_competitor_topology() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, _, _, suppliers = _setup_tender(client, suffix="E5")
        mission = client.get(f"/v1/missions/{mission_id}")
        assert mission.status_code == 200
        buyer_id = str(mission.json()["buyer_id"])

        response = client.get(
            f"/v1/evidence/missions/{mission_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert response.status_code == 200
        assert response.json()["completeness"] == "complete"
        source_types = {item["source_type"] for item in response.json()["sources"]}
        assert "tender" not in source_types
        assert "rfq" not in source_types
        assert "quote" not in source_types
        assert "canonical_json" not in response.text

        for supplier in suppliers:
            assert supplier["quote_id"] not in response.text
            assert supplier["rfq_id"] not in response.text
            assert supplier["operator_id"] not in response.text


@pytest.mark.integration
def test_pr25_disruption_and_final_reconciliation_reconstruct_exact_lineage() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="E6")
        booking_id = str(setup["booking_id"])
        buyer_id = str(setup["buyer_id"])
        other_buyer_id = str(setup["other_buyer_id"])
        operator_id = str(setup["operator_id"])
        other_operator_id = str(setup["other_operator_id"])

        created = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-disruption-e6",
            },
            json={
                "disruption_type": "other",
                "reason": "Post-booking commercial operating adjustment",
            },
        )
        assert created.status_code == 201
        disruption_id = str(created.json()["id"])

        proposal = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-proposal-e6",
            },
            json={
                "source": "operations",
                "source_evidence": "Original aircraft and schedule retained",
            },
        )
        assert proposal.status_code == 201
        proposal_id = str(proposal.json()["id"])

        change = client.post(
            f"/v1/disruptions/{disruption_id}/requotes",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-change-e6",
            },
            json={
                "proposal_id": proposal_id,
                "currency": "EUR",
                "known_adjustment_minor": 125_000,
                "conditional_adjustment_minor": 25_000,
                "terms_summary": "Buyer-approved post-booking commercial adjustment",
            },
        )
        assert change.status_code == 201
        change_id = str(change.json()["id"])

        buyer_decision = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-decision-e6",
            },
            json={
                "proposal_id": proposal_id,
                "commercial_change_id": change_id,
                "decision": "approved",
            },
        )
        assert buyer_decision.status_code == 201
        decision_id = str(buyer_decision.json()["id"])

        resolved = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-resolve-e6",
            },
            json={
                "proposal_id": proposal_id,
                "outcome": "Commercial adjustment accepted; original operation retained",
            },
        )
        assert resolved.status_code == 200

        disruption_evidence = client.get(
            f"/v1/evidence/disruptions/{disruption_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert disruption_evidence.status_code == 200
        disruption_source = next(
            item
            for item in disruption_evidence.json()["sources"]
            if item["source_type"] == "disruption"
        )
        assert disruption_source["facts"]["selected_proposal_id"] == proposal_id
        assert disruption_source["facts"]["selected_commercial_change_id"] == change_id
        assert disruption_source["facts"]["selected_buyer_decision_id"] == decision_id
        assert (
            client.get(
                f"/v1/evidence/disruptions/{disruption_id}",
                headers={"X-Buyer-Id": other_buyer_id},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/evidence/disruptions/{disruption_id}",
                headers={"X-Operator-Id": other_operator_id},
            ).status_code
            == 404
        )

        _create_accepted_contract(client, booking_id=booking_id, suffix="e6")
        for ordinal, command in enumerate(
            (
                "mark-contracted",
                "mark-payment-pending",
                "confirm",
                "enter-pre-operation",
                "start-operation",
                "complete",
            ),
            start=1,
        ):
            response = client.post(
                f"/v1/bookings/{booking_id}/{command}",
                headers={"Idempotency-Key": f"pr25-{command}-e6-{ordinal}"},
            )
            assert response.status_code == 200

        opened = client.post(
            f"/v1/bookings/{booking_id}/reconciliation",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-reconciliation-e6",
            },
        )
        assert opened.status_code == 201
        reconciliation = opened.json()
        reconciliation_id = str(reconciliation["id"])
        assert reconciliation["booked_amount_minor"] == 8_275_000
        assert reconciliation["booked_worst_case_amount_minor"] == 8_330_000

        invoice = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-invoice-e6",
            },
            json={
                "invoice_reference": "INV-PR25-E6",
                "currency": "EUR",
                "total_amount_minor": 8_375_000,
                "line_items": [
                    {
                        "category": "charter_base",
                        "label": "Booked charter and approved disruption adjustment",
                        "amount_minor": 8_275_000,
                    },
                    {
                        "category": "fuel_surcharge",
                        "label": "Final fuel variance",
                        "amount_minor": 100_000,
                        "reason": "Post-operation fuel variance",
                    },
                ],
                "surcharge_reason": "Final fuel variance",
            },
        )
        assert invoice.status_code == 201

        variance = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr25-variance-e6",
            },
            json={"approved_variance_minor": 100_000},
        )
        assert variance.status_code == 201

        completed = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr25-complete-e6",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["final_payable_minor"] == 8_375_000

        evidence = client.get(
            f"/v1/evidence/reconciliations/{reconciliation_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert evidence.status_code == 200
        package = evidence.json()
        assert package["completeness"] == "complete"
        assert "canonical_json" not in evidence.text

        source_by_type = {
            item["source_type"]: item
            for item in package["sources"]
            if item["source_type"] in {"financial_reconciliation", "disruption_commercial_change"}
        }
        assert (
            source_by_type["financial_reconciliation"]["facts"]["final_payable_minor"] == 8_375_000
        )
        assert source_by_type["disruption_commercial_change"]["source_id"] == change_id

        opening_event = next(
            item
            for item in package["events"]
            if item["event_type"] == "FINANCIAL_RECONCILIATION_OPENED"
        )
        assert change_id in opening_event["decision_output"]["commercial_change_ids"]

        event_types = {item["event_type"] for item in package["events"]}
        assert "FINANCIAL_RECONCILIATION_COMPLETED" in event_types
        assert "BOOKING_RECONCILED" in event_types
        assert (
            client.get(
                f"/v1/evidence/reconciliations/{reconciliation_id}",
                headers={"X-Operator-Id": operator_id},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/v1/evidence/reconciliations/{reconciliation_id}",
                headers={"X-Buyer-Id": other_buyer_id},
            ).status_code
            == 404
        )
