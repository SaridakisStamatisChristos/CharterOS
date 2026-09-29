from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from apps.api.main import create_app
from charteros.application.graph_queries import GraphQueryService
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import AirportRow, OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphAggregateCursorRow,
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
    GraphProjectionVersionRow,
)
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.infrastructure.db.repositories.graph_queries import (
    SqlAlchemyGraphQueryRepository,
)
from charteros.shared.config import Settings
from tests.integration.matching_support import insert_profile, setup_matching_state

BASE = datetime(2026, 10, 1, tzinfo=UTC)
VERSION = 1601


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _node(
    *,
    node_type: str,
    node_id: UUID,
    attributes: dict[str, object],
    source_type: str | None = None,
) -> GraphNodeRow:
    return GraphNodeRow(
        projection_name="charter_graph",
        projection_version=VERSION,
        node_type=node_type,
        node_id=node_id,
        attributes=attributes,
        source_aggregate_type=source_type or node_type,
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


def _airport(
    airport_id: UUID,
    *,
    icao: str,
    latitude: str,
    longitude: str,
) -> AirportRow:
    return AirportRow(
        id=airport_id,
        version=1,
        icao=icao,
        iata=None,
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        timezone="Europe/Athens",
        runway_metadata={},
        curfew_metadata={},
        operational_flags=[],
    )


def _quote_event(
    quote_id: UUID,
    *,
    version: int,
    event_type: str,
    when: datetime,
    payload: dict[str, object],
) -> OutboxEventRow:
    event_id = uuid4()
    document = {
        "event_id": str(event_id),
        "aggregate_type": "quote",
        "aggregate_id": str(quote_id),
        "aggregate_version": version,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": when.isoformat().replace("+00:00", "Z"),
        "recorded_at": when.isoformat().replace("+00:00", "Z"),
        "actor_id": None,
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }
    return OutboxEventRow(
        event_id=event_id,
        aggregate_type="quote",
        aggregate_id=quote_id,
        aggregate_version=version,
        event_type=event_type,
        event_version=1,
        occurred_at=when,
        recorded_at=when,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=json.dumps(document, sort_keys=True, separators=(",", ":")),
        delivery_status="pending",
        available_at=when,
        last_attempt_at=None,
        last_error=None,
        lease_owner=None,
        lease_token=None,
        lease_expires_at=None,
        published_at=None,
        poisoned_at=None,
        publish_attempts=0,
        delivery_attempts=0,
    )


@pytest.mark.integration
def test_pr16_graph_queries_are_bounded_bitemporal_and_lineage_preserving() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    airport_a = uuid4()
    airport_b = uuid4()
    airport_c = uuid4()
    operator_id = uuid4()
    aircraft_id = uuid4()
    position_early = uuid4()
    position_late = uuid4()
    mission_one = uuid4()
    mission_two = uuid4()
    booking_one = uuid4()
    booking_two = uuid4()
    quote_v1 = uuid4()
    quote_v2 = uuid4()
    quote_two = uuid4()
    rfq_one = uuid4()
    rfq_two = uuid4()

    first_departure = BASE + timedelta(hours=10)
    first_departure_end = first_departure + timedelta(hours=2)
    second_departure = BASE + timedelta(hours=16)
    second_departure_end = second_departure + timedelta(hours=2)

    try:
        with factory.begin() as session:
            session.add_all(
                [
                    _airport(
                        airport_a,
                        icao="LGAA",
                        latitude="37.9364",
                        longitude="23.9445",
                    ),
                    _airport(
                        airport_b,
                        icao="LGBB",
                        latitude="40.5197",
                        longitude="22.9709",
                    ),
                    _airport(
                        airport_c,
                        icao="LGCC",
                        latitude="35.3397",
                        longitude="25.1803",
                    ),
                    GraphProjectionVersionRow(
                        projection_name="charter_graph",
                        projection_version=VERSION,
                        status="active",
                        active_key="active",
                        created_at=BASE,
                        verified_at=BASE,
                        activated_at=BASE,
                        state_digest=None,
                        event_count=0,
                    ),
                ]
            )
            session.add_all(
                [
                    _node(node_type="airport", node_id=airport_a, attributes={"icao": "LGAA"}),
                    _node(node_type="airport", node_id=airport_b, attributes={"icao": "LGBB"}),
                    _node(node_type="airport", node_id=airport_c, attributes={"icao": "LGCC"}),
                    _node(node_type="operator", node_id=operator_id, attributes={}),
                    _node(
                        node_type="aircraft",
                        node_id=aircraft_id,
                        attributes={"status": "active"},
                    ),
                    _node(
                        node_type="aircraft_position",
                        node_id=position_early,
                        attributes={
                            "position_id": str(position_early),
                            "airport_id": str(airport_a),
                            "latitude": None,
                            "longitude": None,
                            "event_time": (BASE + timedelta(hours=7)).isoformat(),
                            "knowledge_time": (BASE + timedelta(hours=7)).isoformat(),
                            "source": "pr16-test",
                            "provenance": {"ordinal": 1},
                        },
                        source_type="aircraft",
                    ),
                    _node(
                        node_type="aircraft_position",
                        node_id=position_late,
                        attributes={
                            "position_id": str(position_late),
                            "airport_id": str(airport_b),
                            "latitude": None,
                            "longitude": None,
                            "event_time": (BASE + timedelta(hours=8, minutes=30)).isoformat(),
                            "knowledge_time": (BASE + timedelta(hours=12)).isoformat(),
                            "source": "pr16-late-test",
                            "provenance": {"ordinal": 2},
                        },
                        source_type="aircraft",
                    ),
                    _node(
                        node_type="mission",
                        node_id=mission_one,
                        attributes={
                            "departure_from": first_departure.isoformat(),
                            "departure_to": first_departure_end.isoformat(),
                            "status": "booked",
                        },
                    ),
                    _node(
                        node_type="mission",
                        node_id=mission_two,
                        attributes={
                            "departure_from": second_departure.isoformat(),
                            "departure_to": second_departure_end.isoformat(),
                            "status": "booked",
                        },
                    ),
                    _node(
                        node_type="booking",
                        node_id=booking_one,
                        attributes={"state": "confirmed"},
                    ),
                    _node(
                        node_type="booking",
                        node_id=booking_two,
                        attributes={"state": "confirmed"},
                    ),
                    _node(
                        node_type="quote",
                        node_id=quote_v1,
                        attributes={
                            "rfq_id": str(rfq_one),
                            "revision_number": 1,
                            "status": "superseded",
                            "supersedes_quote_id": None,
                        },
                    ),
                    _node(
                        node_type="quote",
                        node_id=quote_v2,
                        attributes={
                            "rfq_id": str(rfq_one),
                            "revision_number": 2,
                            "status": "accepted",
                            "supersedes_quote_id": str(quote_v1),
                        },
                    ),
                    _node(
                        node_type="quote",
                        node_id=quote_two,
                        attributes={
                            "rfq_id": str(rfq_two),
                            "revision_number": 1,
                            "status": "accepted",
                            "supersedes_quote_id": None,
                        },
                    ),
                    _node(node_type="rfq", node_id=rfq_one, attributes={"status": "quoted"}),
                    _node(node_type="rfq", node_id=rfq_two, attributes={"status": "quoted"}),
                ]
            )
            session.add_all(
                [
                    _edge(
                        edge_type="HAS_POSITION",
                        source_type="aircraft",
                        source_id=aircraft_id,
                        target_type="aircraft_position",
                        target_id=position_early,
                    ),
                    _edge(
                        edge_type="HAS_POSITION",
                        source_type="aircraft",
                        source_id=aircraft_id,
                        target_type="aircraft_position",
                        target_id=position_late,
                    ),
                    _edge(
                        edge_type="ORIGIN",
                        source_type="mission",
                        source_id=mission_one,
                        target_type="airport",
                        target_id=airport_a,
                    ),
                    _edge(
                        edge_type="DESTINATION",
                        source_type="mission",
                        source_id=mission_one,
                        target_type="airport",
                        target_id=airport_b,
                    ),
                    _edge(
                        edge_type="ORIGIN",
                        source_type="mission",
                        source_id=mission_two,
                        target_type="airport",
                        target_id=airport_c,
                    ),
                    _edge(
                        edge_type="DESTINATION",
                        source_type="mission",
                        source_id=mission_two,
                        target_type="airport",
                        target_id=airport_a,
                    ),
                    _edge(
                        edge_type="HAS_BOOKING",
                        source_type="mission",
                        source_id=mission_one,
                        target_type="booking",
                        target_id=booking_one,
                    ),
                    _edge(
                        edge_type="HAS_BOOKING",
                        source_type="mission",
                        source_id=mission_two,
                        target_type="booking",
                        target_id=booking_two,
                    ),
                    _edge(
                        edge_type="WITH_OPERATOR",
                        source_type="booking",
                        source_id=booking_one,
                        target_type="operator",
                        target_id=operator_id,
                    ),
                    _edge(
                        edge_type="WITH_OPERATOR",
                        source_type="booking",
                        source_id=booking_two,
                        target_type="operator",
                        target_id=operator_id,
                    ),
                    _edge(
                        edge_type="USES_AIRCRAFT",
                        source_type="booking",
                        source_id=booking_one,
                        target_type="aircraft",
                        target_id=aircraft_id,
                    ),
                    _edge(
                        edge_type="USES_AIRCRAFT",
                        source_type="booking",
                        source_id=booking_two,
                        target_type="aircraft",
                        target_id=aircraft_id,
                    ),
                    _edge(
                        edge_type="ACCEPTED_QUOTE",
                        source_type="booking",
                        source_id=booking_one,
                        target_type="quote",
                        target_id=quote_v2,
                    ),
                    _edge(
                        edge_type="ACCEPTED_QUOTE",
                        source_type="booking",
                        source_id=booking_two,
                        target_type="quote",
                        target_id=quote_two,
                    ),
                    _edge(
                        edge_type="HAS_QUOTE",
                        source_type="rfq",
                        source_id=rfq_one,
                        target_type="quote",
                        target_id=quote_v1,
                    ),
                    _edge(
                        edge_type="HAS_QUOTE",
                        source_type="rfq",
                        source_id=rfq_one,
                        target_type="quote",
                        target_id=quote_v2,
                    ),
                    _edge(
                        edge_type="HAS_QUOTE",
                        source_type="rfq",
                        source_id=rfq_two,
                        target_type="quote",
                        target_id=quote_two,
                    ),
                ]
            )
            session.add_all(
                [
                    _quote_event(
                        quote_v2,
                        version=1,
                        event_type="QUOTE_REVISED",
                        when=BASE + timedelta(hours=9),
                        payload={
                            "rfq_id": str(rfq_one),
                            "revision_number": 2,
                            "supersedes_quote_id": str(quote_v1),
                        },
                    ),
                    _quote_event(
                        quote_v2,
                        version=2,
                        event_type="QUOTE_ACCEPTED",
                        when=BASE + timedelta(hours=9, minutes=30),
                        payload={"booking_id": str(booking_one)},
                    ),
                ]
            )

        with factory() as session:
            service = GraphQueryService(SqlAlchemyGraphQueryRepository(session))

            historical = service.historical_position(
                aircraft_id=aircraft_id,
                event_time=BASE + timedelta(hours=11),
                known_as_of=BASE + timedelta(hours=11),
            )
            assert historical.position_id == position_early
            assert historical.airport_id == airport_a

            later_known = service.historical_position(
                aircraft_id=aircraft_id,
                event_time=BASE + timedelta(hours=11),
                known_as_of=BASE + timedelta(hours=13),
            )
            assert later_known.position_id == position_late
            assert later_known.airport_id == airport_b

            nearby = service.nearby_aircraft(
                airport_id=airport_a,
                event_time=BASE + timedelta(hours=11),
                known_as_of=BASE + timedelta(hours=11),
                radius_nm=Decimal("5"),
                limit=10,
            )
            assert len(nearby) == 1
            assert nearby[0].position.aircraft_id == aircraft_id
            assert nearby[0].distance_nm == Decimal("0.0")

            routes = service.operator_route_history(operator_id=operator_id, limit=10)
            assert [item.booking_id for item in routes] == [booking_two, booking_one]
            assert routes[0].origin_airport_id == airport_c
            assert routes[1].destination_airport_id == airport_b

            history = service.quote_history(quote_id=quote_v2)
            assert [item.quote_id for item in history.revisions] == [quote_v1, quote_v2]
            assert [item.event_type for item in history.events] == [
                "QUOTE_REVISED",
                "QUOTE_ACCEPTED",
            ]

            empty_legs = service.empty_leg_candidates(
                window_start=first_departure_end,
                window_end=second_departure,
                limit=10,
            )
            assert len(empty_legs) == 1
            assert empty_legs[0].from_airport_id == airport_b
            assert empty_legs[0].to_airport_id == airport_c
            assert empty_legs[0].gap_minutes == 240

            lineage = service.booking_flight_lineage(booking_id=booking_one)
            assert lineage.mission_id == mission_one
            assert lineage.accepted_quote_id == quote_v2
            assert lineage.operator_id == operator_id
            assert lineage.aircraft_id == aircraft_id
            assert lineage.flight_entity_id is None
            assert lineage.lineage_status == "planned_route_only"
    finally:
        with factory.begin() as session:
            session.execute(
                delete(OutboxEventRow).where(
                    OutboxEventRow.aggregate_type == "quote",
                    OutboxEventRow.aggregate_id == quote_v2,
                )
            )
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
                delete(GraphProjectionVersionRow).where(
                    GraphProjectionVersionRow.projection_name == "charter_graph",
                    GraphProjectionVersionRow.projection_version == VERSION,
                )
            )
            session.execute(
                delete(AirportRow).where(AirportRow.id.in_((airport_a, airport_b, airport_c)))
            )
        engine.dispose()


@pytest.mark.integration
def test_pr16_feasible_aircraft_query_reuses_matching_policy_on_active_projection() -> None:
    settings = _settings()
    projection_version = 1602
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with TestClient(create_app(settings)) as client:
            mission_id, aircraft_id, _departure, aircraft_type_id, _availability_id = (
                setup_matching_state(client, suffix="GQ")
            )
            insert_profile(
                settings,
                aircraft_type_id,
                recorded_at=datetime.now(UTC) - timedelta(minutes=1),
            )

            store = SqlAlchemyGraphProjectionStore(factory)
            report = store.rebuild(projection_version, now=datetime.now(UTC))
            assert report.ok, report.issues
            store.activate(
                projection_version,
                now=datetime.now(UTC),
                maintenance_mode=True,
            )

            response = client.get(
                f"/v1/graph/missions/{mission_id}/feasible-aircraft",
                params={
                    "known_as_of": datetime.now(UTC).isoformat(),
                    "limit": 10,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["projection_version"] == projection_version
            assert body["mission_id"] == mission_id
            assert body["feasible_count"] >= 1
            assert any(item["aircraft_id"] == aircraft_id for item in body["aircraft"])
            assert body["policy_version"]
    finally:
        with factory.begin() as session:
            session.execute(
                delete(OutboxConsumerReceiptRow).where(
                    OutboxConsumerReceiptRow.consumer_name == f"charter_graph:v{projection_version}"
                )
            )
            session.execute(
                delete(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == "charter_graph",
                    GraphEdgeRow.projection_version == projection_version,
                )
            )
            session.execute(
                delete(GraphNodeRow).where(
                    GraphNodeRow.projection_name == "charter_graph",
                    GraphNodeRow.projection_version == projection_version,
                )
            )
            session.execute(
                delete(GraphAggregateCursorRow).where(
                    GraphAggregateCursorRow.projection_name == "charter_graph",
                    GraphAggregateCursorRow.projection_version == projection_version,
                )
            )
            session.execute(
                delete(GraphProjectionCheckpointRow).where(
                    GraphProjectionCheckpointRow.projection_name == "charter_graph",
                    GraphProjectionCheckpointRow.projection_version == projection_version,
                )
            )
            session.execute(
                delete(GraphProjectionVersionRow).where(
                    GraphProjectionVersionRow.projection_name == "charter_graph",
                    GraphProjectionVersionRow.projection_version == projection_version,
                )
            )
        engine.dispose()
