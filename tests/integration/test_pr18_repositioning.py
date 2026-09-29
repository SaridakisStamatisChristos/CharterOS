import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from apps.api.main import create_app
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import (
    AircraftRow,
    AircraftTypeRow,
    AirportRow,
    OperatorRow,
    OrganizationRow,
)
from charteros.infrastructure.db.models.graph import (
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
    GraphProjectionVersionRow,
)
from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.shared.config import Settings

BASE = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
VERSION = 1801


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _airport(
    airport_id: UUID,
    *,
    icao: str,
    iata: str,
    latitude: str,
    longitude: str,
) -> AirportRow:
    return AirportRow(
        id=airport_id,
        version=1,
        icao=icao,
        iata=iata,
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        timezone="UTC",
        runway_metadata={},
        curfew_metadata={},
        operational_flags=[],
    )


def _node(
    *,
    node_type: str,
    node_id: UUID,
    attributes: dict[str, object],
) -> GraphNodeRow:
    return GraphNodeRow(
        projection_name="charter_graph",
        projection_version=VERSION,
        node_type=node_type,
        node_id=node_id,
        attributes=attributes,
        source_aggregate_type=node_type,
        source_aggregate_id=node_id,
        source_aggregate_version=1,
        last_event_id=uuid4(),
        updated_at=BASE,
    )


def _edge(
    *,
    edge_type: str,
    source_type: str,
    source_id: UUID,
    target_type: str,
    target_id: UUID,
) -> GraphEdgeRow:
    return GraphEdgeRow(
        projection_name="charter_graph",
        projection_version=VERSION,
        edge_type=edge_type,
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        attributes={},
        source_aggregate_type=source_type,
        source_aggregate_id=source_id,
        source_aggregate_version=1,
        last_event_id=uuid4(),
        updated_at=BASE,
    )


@pytest.mark.integration
def test_pr18_optimizer_fills_synthetic_graph_empty_leg_with_profitable_future_mission() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    airport_a, airport_b, airport_c, airport_d = (uuid4() for _ in range(4))
    operator_org = uuid4()
    buyer_org = uuid4()
    operator_id = uuid4()
    aircraft_type_id = uuid4()
    aircraft_id = uuid4()
    profile_id = uuid4()

    previous_mission = uuid4()
    next_mission = uuid4()
    previous_booking = uuid4()
    next_booking = uuid4()
    previous_quote = uuid4()
    next_quote = uuid4()

    opportunity_mission = uuid4()
    rfq_id = uuid4()
    quote_id = uuid4()

    evaluated_at = BASE + timedelta(hours=9)
    previous_from = BASE + timedelta(hours=10)
    previous_to = BASE + timedelta(hours=11)
    opportunity_from = BASE + timedelta(hours=12)
    opportunity_to = BASE + timedelta(hours=13)
    next_from = BASE + timedelta(hours=16)
    next_to = BASE + timedelta(hours=17)

    try:
        with factory.begin() as session:
            session.add_all(
                [
                    _airport(
                        airport_a,
                        icao="LGAA",
                        iata="GAA",
                        latitude="37.90000",
                        longitude="23.90000",
                    ),
                    _airport(
                        airport_b,
                        icao="LGBB",
                        iata="GBB",
                        latitude="38.50000",
                        longitude="24.00000",
                    ),
                    _airport(
                        airport_c,
                        icao="LGCC",
                        iata="GCC",
                        latitude="39.20000",
                        longitude="24.60000",
                    ),
                    _airport(
                        airport_d,
                        icao="LGDD",
                        iata="GDD",
                        latitude="38.90000",
                        longitude="24.30000",
                    ),
                    OrganizationRow(
                        id=operator_org,
                        version=1,
                        type="operator",
                        legal_name="PR18 Operator",
                        legal_name_key="pr18 operator",
                        trading_name=None,
                        country="GR",
                        status="active",
                    ),
                    OrganizationRow(
                        id=buyer_org,
                        version=1,
                        type="buyer",
                        legal_name="PR18 Buyer",
                        legal_name_key="pr18 buyer",
                        trading_name=None,
                        country="GR",
                        status="active",
                    ),
                ]
            )
            session.flush()
            session.add(
                OperatorRow(
                    id=operator_id,
                    version=1,
                    organization_id=operator_org,
                    aoc_reference=f"PR18-{operator_id.hex[:8]}",
                    operating_regions=["EU"],
                    verification_status="verified",
                    insurance_status="valid",
                    safety_documents=[],
                    commercial_status="active",
                )
            )
            session.add(
                AircraftTypeRow(
                    id=aircraft_type_id,
                    manufacturer="PR18 Airframes",
                    manufacturer_key="pr18 airframes",
                    model=f"FlowJet-{aircraft_type_id.hex[:8]}",
                    model_key=f"flowjet-{aircraft_type_id.hex[:8]}",
                    category="regional",
                    seats_min=1,
                    seats_max=20,
                    range_nm=3000,
                    runway_requirements={},
                    baggage_cargo_profile={},
                )
            )
            session.flush()
            session.add(
                AircraftRow(
                    id=aircraft_id,
                    version=1,
                    operator_id=operator_id,
                    registration=f"P18-{aircraft_id.hex[:8].upper()}",
                    aircraft_type_id=aircraft_type_id,
                    seat_capacity=10,
                    cargo_capacity=Decimal("500"),
                    range_nm=2500,
                    home_base_id=airport_a,
                    status="active",
                )
            )
            session.add(
                MatchingReferenceProfileRow(
                    id=profile_id,
                    aircraft_type_id=aircraft_type_id,
                    cruise_speed_kts=400,
                    operating_cost_per_hour_minor=100_000,
                    operating_cost_currency="EUR",
                    max_reposition_nm=1500,
                    turnaround_buffer_minutes=20,
                    source="pr18-synthetic",
                    provenance={"scenario": "profitable-empty-leg"},
                    recorded_at=evaluated_at - timedelta(hours=1),
                    superseded_at=None,
                )
            )
            session.add(
                MissionRow(
                    id=opportunity_mission,
                    version=2,
                    buyer_id=buyer_org,
                    origin_airport_id=airport_b,
                    destination_airport_id=airport_d,
                    departure_from=opportunity_from,
                    departure_to=opportunity_to,
                    passenger_count=6,
                    max_budget_amount_minor=None,
                    max_budget_currency=None,
                    special_requirements=[],
                    status="sourcing",
                    created_at=evaluated_at - timedelta(hours=2),
                )
            )
            session.flush()
            session.add(
                RfqRow(
                    id=rfq_id,
                    version=3,
                    mission_id=opportunity_mission,
                    operator_id=operator_id,
                    status="quoted",
                    created_at=evaluated_at - timedelta(hours=2),
                    sent_at=evaluated_at - timedelta(hours=2),
                    response_deadline=opportunity_from - timedelta(hours=1),
                    acknowledged_at=evaluated_at - timedelta(hours=1, minutes=50),
                    declined_at=None,
                    expired_at=None,
                    decline_reason=None,
                )
            )
            session.flush()
            session.add(
                QuoteRow(
                    id=quote_id,
                    version=1,
                    rfq_id=rfq_id,
                    aircraft_id=aircraft_id,
                    currency="EUR",
                    base_amount_minor=2_000_000,
                    repositioning_amount_minor=0,
                    inclusions=[],
                    exclusions=[],
                    cancellation_terms=None,
                    payment_terms=None,
                    valid_until=opportunity_from - timedelta(minutes=30),
                    status="submitted",
                    revision_number=1,
                    supersedes_quote_id=None,
                    submitted_at=evaluated_at - timedelta(hours=1),
                    is_current=True,
                    accepted_at=None,
                    rejected_at=None,
                    expired_at=None,
                    withdrawn_at=None,
                    superseded_at=None,
                )
            )

            session.add(
                GraphProjectionVersionRow(
                    projection_name="charter_graph",
                    projection_version=VERSION,
                    status="active",
                    active_key="active",
                    created_at=BASE,
                    verified_at=BASE,
                    activated_at=BASE,
                    state_digest=None,
                    event_count=1,
                )
            )
            session.flush()
            session.add(
                GraphProjectionCheckpointRow(
                    projection_name="charter_graph",
                    projection_version=VERSION,
                    processed_event_count=1,
                    max_recorded_at=evaluated_at - timedelta(minutes=30),
                    max_recorded_event_id=uuid4(),
                    updated_at=evaluated_at - timedelta(minutes=30),
                )
            )
            session.add_all(
                [
                    _node(
                        node_type="mission",
                        node_id=previous_mission,
                        attributes={
                            "departure_from": previous_from.isoformat(),
                            "departure_to": previous_to.isoformat(),
                            "status": "booked",
                        },
                    ),
                    _node(
                        node_type="mission",
                        node_id=next_mission,
                        attributes={
                            "departure_from": next_from.isoformat(),
                            "departure_to": next_to.isoformat(),
                            "status": "booked",
                        },
                    ),
                    _node(
                        node_type="booking",
                        node_id=previous_booking,
                        attributes={"state": "confirmed"},
                    ),
                    _node(
                        node_type="booking",
                        node_id=next_booking,
                        attributes={"state": "confirmed"},
                    ),
                    _node(
                        node_type="quote",
                        node_id=previous_quote,
                        attributes={
                            "rfq_id": str(uuid4()),
                            "revision_number": 1,
                            "status": "accepted",
                            "supersedes_quote_id": None,
                        },
                    ),
                    _node(
                        node_type="quote",
                        node_id=next_quote,
                        attributes={
                            "rfq_id": str(uuid4()),
                            "revision_number": 1,
                            "status": "accepted",
                            "supersedes_quote_id": None,
                        },
                    ),
                ]
            )
            session.add_all(
                [
                    _edge(
                        edge_type="HAS_BOOKING",
                        source_type="mission",
                        source_id=previous_mission,
                        target_type="booking",
                        target_id=previous_booking,
                    ),
                    _edge(
                        edge_type="HAS_BOOKING",
                        source_type="mission",
                        source_id=next_mission,
                        target_type="booking",
                        target_id=next_booking,
                    ),
                    _edge(
                        edge_type="ORIGIN",
                        source_type="mission",
                        source_id=previous_mission,
                        target_type="airport",
                        target_id=airport_a,
                    ),
                    _edge(
                        edge_type="DESTINATION",
                        source_type="mission",
                        source_id=previous_mission,
                        target_type="airport",
                        target_id=airport_b,
                    ),
                    _edge(
                        edge_type="ORIGIN",
                        source_type="mission",
                        source_id=next_mission,
                        target_type="airport",
                        target_id=airport_c,
                    ),
                    _edge(
                        edge_type="DESTINATION",
                        source_type="mission",
                        source_id=next_mission,
                        target_type="airport",
                        target_id=airport_a,
                    ),
                    _edge(
                        edge_type="WITH_OPERATOR",
                        source_type="booking",
                        source_id=previous_booking,
                        target_type="operator",
                        target_id=operator_id,
                    ),
                    _edge(
                        edge_type="WITH_OPERATOR",
                        source_type="booking",
                        source_id=next_booking,
                        target_type="operator",
                        target_id=operator_id,
                    ),
                    _edge(
                        edge_type="USES_AIRCRAFT",
                        source_type="booking",
                        source_id=previous_booking,
                        target_type="aircraft",
                        target_id=aircraft_id,
                    ),
                    _edge(
                        edge_type="USES_AIRCRAFT",
                        source_type="booking",
                        source_id=next_booking,
                        target_type="aircraft",
                        target_id=aircraft_id,
                    ),
                    _edge(
                        edge_type="ACCEPTED_QUOTE",
                        source_type="booking",
                        source_id=previous_booking,
                        target_type="quote",
                        target_id=previous_quote,
                    ),
                    _edge(
                        edge_type="ACCEPTED_QUOTE",
                        source_type="booking",
                        source_id=next_booking,
                        target_type="quote",
                        target_id=next_quote,
                    ),
                ]
            )

        with TestClient(create_app(settings)) as client:
            response = client.get(
                "/v1/optimization/repositioning",
                params={
                    "window_start": previous_to.isoformat(),
                    "window_end": next_from.isoformat(),
                    "evaluated_at": evaluated_at.isoformat(),
                    "empty_leg_limit": 10,
                    "opportunity_limit": 100,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["projection_version"] == VERSION
            assert body["policy_version"] == "reposition-v1"
            assert datetime.fromisoformat(body["graph_knowledge_cutoff"]) <= evaluated_at
            assert body["structural_empty_leg_count"] == 1
            assert body["evaluable_empty_leg_count"] == 1
            assert body["direct_reposition_feasible_count"] == 1
            assert body["quoted_future_leg_count"] == 1
            assert body["feasible_candidate_count"] == 1
            assert body["global_plan_available"] is True
            assert len(body["currency_plans"]) == 1
            plan = body["currency_plans"][0]
            assert plan["currency"] == "EUR"
            assert plan["assignment_count"] == 1
            assignment = plan["assignments"][0]
            assert assignment["aircraft_id"] == str(aircraft_id)
            assert assignment["operator_id"] == str(operator_id)
            assert assignment["mission_id"] == str(opportunity_mission)
            assert assignment["quote_id"] == str(quote_id)
            assert assignment["previous_booking_id"] == str(previous_booking)
            assert assignment["next_booking_id"] == str(next_booking)
            assert assignment["margin_minor"] > 0
            assert assignment["baseline_reposition_feasible"] is True
            assert assignment["opportunity_cost_minor"] >= 0
            assert Decimal(assignment["previous_revenue_distance_nm"]) > 0
            assert assignment["previous_revenue_minutes"] > 0
            assert datetime.fromisoformat(assignment["aircraft_available_at"]) > previous_to
            assert datetime.fromisoformat(assignment["continuity_ready_at"]) <= next_from
    finally:
        with factory.begin() as session:
            session.execute(
                delete(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == "charter_graph",
                    GraphEdgeRow.projection_version == VERSION,
                )
            )
            session.execute(
                delete(GraphNodeRow).where(
                    GraphNodeRow.projection_name == "charter_graph",
                    GraphNodeRow.projection_version == VERSION,
                )
            )
            session.execute(
                delete(GraphProjectionCheckpointRow).where(
                    GraphProjectionCheckpointRow.projection_name == "charter_graph",
                    GraphProjectionCheckpointRow.projection_version == VERSION,
                )
            )
            session.execute(
                delete(GraphProjectionVersionRow).where(
                    GraphProjectionVersionRow.projection_name == "charter_graph",
                    GraphProjectionVersionRow.projection_version == VERSION,
                )
            )
            session.execute(delete(QuoteRow).where(QuoteRow.id == quote_id))
            session.execute(delete(RfqRow).where(RfqRow.id == rfq_id))
            session.execute(delete(MissionRow).where(MissionRow.id == opportunity_mission))
            session.execute(
                delete(MatchingReferenceProfileRow).where(
                    MatchingReferenceProfileRow.id == profile_id
                )
            )
            session.execute(delete(AircraftRow).where(AircraftRow.id == aircraft_id))
            session.execute(delete(AircraftTypeRow).where(AircraftTypeRow.id == aircraft_type_id))
            session.execute(delete(OperatorRow).where(OperatorRow.id == operator_id))
            session.execute(
                delete(OrganizationRow).where(OrganizationRow.id.in_((operator_org, buyer_org)))
            )
            session.execute(
                delete(AirportRow).where(
                    AirportRow.id.in_((airport_a, airport_b, airport_c, airport_d))
                )
            )
        engine.dispose()
