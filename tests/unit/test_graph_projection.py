import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from charteros.application.graph_projection import (
    GraphProjectionGapError,
    GraphReferenceState,
    UnsupportedGraphEventError,
    graph_mutation_for,
    reference_state_from,
)
from charteros.application.outbox import OutboxEnvelope

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _envelope(
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    payload: dict[str, object],
    event_id: UUID | None = None,
) -> OutboxEnvelope:
    chosen_id = event_id or uuid4()
    document: dict[str, object] = {
        "event_id": str(chosen_id),
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "aggregate_version": aggregate_version,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": "2026-09-29T00:00:00Z",
        "recorded_at": "2026-09-29T00:00:00Z",
        "actor_id": None,
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }
    return OutboxEnvelope(
        event_id=chosen_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        event_version=1,
        occurred_at=NOW,
        recorded_at=NOW,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=json.dumps(document, sort_keys=True, separators=(",", ":")),
    )


def test_aircraft_registration_projects_node_and_relations() -> None:
    aircraft_id = uuid4()
    operator_id = uuid4()
    airport_id = uuid4()
    envelope = _envelope(
        aggregate_type="aircraft",
        aggregate_id=aircraft_id,
        aggregate_version=1,
        event_type="AIRCRAFT_REGISTERED",
        payload={
            "operator_id": str(operator_id),
            "registration": "SX-PR15",
            "aircraft_type_id": str(uuid4()),
            "home_base_id": str(airport_id),
            "status": "active",
        },
    )

    mutation = graph_mutation_for(envelope)

    assert [(item.node_type, item.node_id) for item in mutation.node_upserts] == [
        ("aircraft", aircraft_id)
    ]
    assert {(edge.edge_type, edge.source_id, edge.target_id) for edge in mutation.edge_upserts} == {
        ("OPERATES", operator_id, aircraft_id),
        ("HOME_BASE", aircraft_id, airport_id),
    }


def test_booking_projects_lineage_edges() -> None:
    booking_id = uuid4()
    mission_id = uuid4()
    quote_id = uuid4()
    operator_id = uuid4()
    aircraft_id = uuid4()
    envelope = _envelope(
        aggregate_type="booking",
        aggregate_id=booking_id,
        aggregate_version=1,
        event_type="BOOKING_CREATED",
        payload={
            "mission_id": str(mission_id),
            "accepted_quote_id": str(quote_id),
            "operator_id": str(operator_id),
            "aircraft_id": str(aircraft_id),
            "state": "pending_contract",
            "created_at": "2026-09-29T00:00:00Z",
        },
    )

    mutation = graph_mutation_for(envelope)

    assert {edge.edge_type for edge in mutation.edge_upserts} == {
        "HAS_BOOKING",
        "ACCEPTED_QUOTE",
        "WITH_OPERATOR",
        "USES_AIRCRAFT",
    }


def test_reference_replay_rejects_aggregate_version_gap() -> None:
    aggregate_id = uuid4()
    event = _envelope(
        aggregate_type="operator",
        aggregate_id=aggregate_id,
        aggregate_version=2,
        event_type="OPERATOR_REGISTERED",
        payload={"organization_id": str(uuid4())},
    )

    with pytest.raises(GraphProjectionGapError, match="expected version 1, got 2"):
        reference_state_from((event,))


def test_unknown_event_on_projected_aggregate_fails_closed() -> None:
    event = _envelope(
        aggregate_type="operator",
        aggregate_id=uuid4(),
        aggregate_version=1,
        event_type="OPERATOR_UNKNOWN_FUTURE_EVENT",
        payload={},
    )

    with pytest.raises(UnsupportedGraphEventError):
        graph_mutation_for(event)


def test_reference_digest_is_deterministic() -> None:
    aggregate_id = uuid4()
    event = _envelope(
        aggregate_type="airport",
        aggregate_id=aggregate_id,
        aggregate_version=1,
        event_type="AIRPORT_REGISTERED",
        payload={"icao": "LGAV", "iata": "ATH", "timezone": "Europe/Athens"},
    )

    first = reference_state_from((event,)).digest()
    second = reference_state_from((event,)).digest()

    assert first == second


def test_charter_graph_testbench_preserves_no_hindsight_position_history() -> None:
    fixture = json.loads(
        Path("test-assets/charter-graph/bitemporal_no_hindsight_v0.1.0.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["source"] == "charter_graph_testbench_v0.1.0"
    aircraft_id = UUID(fixture["aircraft_id"])
    events: list[OutboxEnvelope] = [
        _envelope(
            aggregate_type="aircraft",
            aggregate_id=aircraft_id,
            aggregate_version=1,
            event_type="AIRCRAFT_REGISTERED",
            payload={
                "operator_id": str(uuid4()),
                "registration": "SX-TB01",
                "aircraft_type_id": str(uuid4()),
                "home_base_id": fixture["positions"][0]["airport_id"],
                "status": "active",
            },
        )
    ]
    for version, position in enumerate(fixture["positions"], start=2):
        events.append(
            _envelope(
                aggregate_type="aircraft",
                aggregate_id=aircraft_id,
                aggregate_version=version,
                event_type="AIRCRAFT_POSITION_RECORDED",
                payload={
                    "position_id": position["id"],
                    "airport_id": position["airport_id"],
                    "latitude": None,
                    "longitude": None,
                    "event_time": position["event_time"],
                    "knowledge_time": position["recorded_at"],
                    "source": position["source"],
                    "provenance": {},
                },
            )
        )

    state = reference_state_from(tuple(events))
    positions = {
        str(node_id): attributes
        for (node_type, node_id), attributes in state.nodes.items()
        if node_type == "aircraft_position"
    }

    assert set(positions) == {item["id"] for item in fixture["positions"]}
    assert positions[fixture["positions"][0]["id"]]["knowledge_time"] == (
        fixture["positions"][0]["recorded_at"]
    )
    assert positions[fixture["positions"][1]["id"]]["knowledge_time"] == (
        fixture["positions"][1]["recorded_at"]
    )
    assert GraphReferenceState().digest() != state.digest()
