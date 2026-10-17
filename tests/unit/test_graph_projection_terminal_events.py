import json
from datetime import UTC, datetime
from uuid import uuid4

from charteros.application.graph_projection import graph_mutation_for
from charteros.application.outbox import OutboxEnvelope

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def _envelope(
    *,
    aggregate_type: str,
    event_type: str,
    payload: dict[str, object],
) -> OutboxEnvelope:
    aggregate_id = uuid4()
    event_id = uuid4()
    document: dict[str, object] = {
        "event_id": str(event_id),
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "aggregate_version": 2,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": "2026-09-30T00:00:00Z",
        "recorded_at": "2026-09-30T00:00:00Z",
        "actor_id": None,
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }
    return OutboxEnvelope(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=2,
        event_type=event_type,
        event_version=1,
        occurred_at=NOW,
        recorded_at=NOW,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=json.dumps(document, sort_keys=True, separators=(",", ":")),
    )


def test_pr39_graph_accepts_booking_and_mission_terminal_events() -> None:
    booking = _envelope(
        aggregate_type="booking",
        event_type="BOOKING_CANCELLED",
        payload={
            "from_state": "contracted",
            "to_state": "cancelled",
            "reason": "buyer_cancel",
            "source": "buyer",
            "transitioned_at": "2026-09-30T00:00:00Z",
        },
    )
    mission = _envelope(
        aggregate_type="mission",
        event_type="MISSION_EXPIRED",
        payload={
            "from_status": "selected",
            "status": "expired",
            "reason": "contract_unsigned",
            "source": "system",
            "transitioned_at": "2026-09-30T00:00:00Z",
        },
    )

    booking_mutation = graph_mutation_for(booking)
    mission_mutation = graph_mutation_for(mission)

    assert booking_mutation.node_upserts[0].attributes["state"] == "cancelled"
    assert mission_mutation.node_upserts[0].attributes["status"] == "expired"
