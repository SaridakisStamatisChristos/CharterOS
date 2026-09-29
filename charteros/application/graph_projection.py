from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from charteros.application.outbox import OutboxEnvelope

PROJECTION_NAME = "charter_graph"
CURRENT_PROJECTION_VERSION = 1
SUPPORTED_AGGREGATE_TYPES = frozenset(
    {"operator", "aircraft", "airport", "mission", "rfq", "quote", "booking"}
)


class GraphProjectionError(RuntimeError):
    """Base failure for deterministic graph projection processing."""


class GraphProjectionGapError(GraphProjectionError):
    """Raised when an aggregate event arrives before its causal predecessor."""


class GraphProjectionConsistencyError(GraphProjectionError):
    """Raised when persisted projection history disagrees with an incoming event."""


class UnsupportedGraphEventError(GraphProjectionError):
    """Raised when a projected aggregate emits an event with no explicit mapping."""


@dataclass(frozen=True, slots=True)
class GraphNodeUpsert:
    node_type: str
    node_id: UUID
    attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GraphEdgeUpsert:
    edge_type: str
    source_type: str
    source_id: UUID
    target_type: str
    target_id: UUID
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphMutation:
    node_upserts: tuple[GraphNodeUpsert, ...] = ()
    edge_upserts: tuple[GraphEdgeUpsert, ...] = ()


NodeKey = tuple[str, UUID]
EdgeKey = tuple[str, str, UUID, str, UUID]


class GraphReferenceState:
    """Pure deterministic reference projection used by verification and tests."""

    def __init__(self) -> None:
        self.nodes: dict[NodeKey, dict[str, object]] = {}
        self.edges: dict[EdgeKey, dict[str, object]] = {}

    def apply(self, mutation: GraphMutation) -> None:
        for item in mutation.node_upserts:
            key = (item.node_type, item.node_id)
            merged = dict(self.nodes.get(key, {}))
            merged.update(item.attributes)
            self.nodes[key] = merged
        for item in mutation.edge_upserts:
            key = (
                item.edge_type,
                item.source_type,
                item.source_id,
                item.target_type,
                item.target_id,
            )
            merged = dict(self.edges.get(key, {}))
            merged.update(item.attributes)
            self.edges[key] = merged

    def digest(self) -> str:
        document = {
            "nodes": [
                {
                    "node_type": key[0],
                    "node_id": str(key[1]),
                    "attributes": self.nodes[key],
                }
                for key in sorted(self.nodes, key=lambda item: (item[0], str(item[1])))
            ],
            "edges": [
                {
                    "edge_type": key[0],
                    "source_type": key[1],
                    "source_id": str(key[2]),
                    "target_type": key[3],
                    "target_id": str(key[4]),
                    "attributes": self.edges[key],
                }
                for key in sorted(
                    self.edges,
                    key=lambda item: (
                        item[0],
                        item[1],
                        str(item[2]),
                        item[3],
                        str(item[4]),
                    ),
                )
            ],
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def consumer_name(projection_version: int) -> str:
    if projection_version < 1:
        raise ValueError("projection_version must be positive")
    return f"{PROJECTION_NAME}:v{projection_version}"


def is_projected_aggregate(aggregate_type: str) -> bool:
    return aggregate_type in SUPPORTED_AGGREGATE_TYPES


def graph_mutation_for(envelope: OutboxEnvelope) -> GraphMutation:
    if envelope.aggregate_type not in SUPPORTED_AGGREGATE_TYPES:
        return GraphMutation()
    payload = _event_payload(envelope)
    event_type = envelope.event_type
    aggregate_id = envelope.aggregate_id

    if event_type == "OPERATOR_REGISTERED":
        _expect_aggregate(envelope, "operator")
        return GraphMutation(node_upserts=(GraphNodeUpsert("operator", aggregate_id, payload),))

    if event_type == "AIRPORT_REGISTERED":
        _expect_aggregate(envelope, "airport")
        return GraphMutation(node_upserts=(GraphNodeUpsert("airport", aggregate_id, payload),))

    if event_type == "AIRCRAFT_REGISTERED":
        _expect_aggregate(envelope, "aircraft")
        operator_id = _uuid_field(payload, "operator_id")
        home_base_id = _uuid_field(payload, "home_base_id")
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("aircraft", aggregate_id, payload),),
            edge_upserts=(
                GraphEdgeUpsert(
                    "OPERATES",
                    "operator",
                    operator_id,
                    "aircraft",
                    aggregate_id,
                ),
                GraphEdgeUpsert(
                    "HOME_BASE",
                    "aircraft",
                    aggregate_id,
                    "airport",
                    home_base_id,
                ),
            ),
        )

    if event_type == "AIRCRAFT_POSITION_RECORDED":
        _expect_aggregate(envelope, "aircraft")
        position_id = _uuid_field(payload, "position_id")
        airport_id = _optional_uuid_field(payload, "airport_id")
        edges = [
            GraphEdgeUpsert(
                "HAS_POSITION",
                "aircraft",
                aggregate_id,
                "aircraft_position",
                position_id,
            )
        ]
        if airport_id is not None:
            edges.append(
                GraphEdgeUpsert(
                    "AT_AIRPORT",
                    "aircraft_position",
                    position_id,
                    "airport",
                    airport_id,
                )
            )
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("aircraft_position", position_id, payload),),
            edge_upserts=tuple(edges),
        )

    if event_type == "AIRCRAFT_AVAILABILITY_CHANGED":
        _expect_aggregate(envelope, "aircraft")
        return GraphMutation(
            node_upserts=(
                GraphNodeUpsert(
                    "aircraft",
                    aggregate_id,
                    {"last_availability_change": payload},
                ),
            )
        )

    if event_type == "MISSION_CREATED":
        _expect_aggregate(envelope, "mission")
        origin = _uuid_field(payload, "origin_airport_id")
        destination = _uuid_field(payload, "destination_airport_id")
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("mission", aggregate_id, payload),),
            edge_upserts=(
                GraphEdgeUpsert("ORIGIN", "mission", aggregate_id, "airport", origin),
                GraphEdgeUpsert(
                    "DESTINATION",
                    "mission",
                    aggregate_id,
                    "airport",
                    destination,
                ),
            ),
        )

    if event_type in {
        "MISSION_OPENED",
        "MISSION_SOURCING",
        "MISSION_QUOTED",
        "MISSION_CONTRACTING",
        "MISSION_BOOKED",
        "MISSION_OPERATING",
        "MISSION_COMPLETED",
    }:
        _expect_aggregate(envelope, "mission")
        return GraphMutation(node_upserts=(GraphNodeUpsert("mission", aggregate_id, payload),))

    if event_type == "MISSION_SELECTED":
        _expect_aggregate(envelope, "mission")
        quote_id = _uuid_field(payload, "quote_id")
        booking_id = _uuid_field(payload, "booking_id")
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("mission", aggregate_id, payload),),
            edge_upserts=(
                GraphEdgeUpsert(
                    "SELECTED_QUOTE",
                    "mission",
                    aggregate_id,
                    "quote",
                    quote_id,
                ),
                GraphEdgeUpsert(
                    "SELECTED_BOOKING",
                    "mission",
                    aggregate_id,
                    "booking",
                    booking_id,
                ),
            ),
        )

    if event_type == "RFQ_CREATED":
        _expect_aggregate(envelope, "rfq")
        mission_id = _uuid_field(payload, "mission_id")
        operator_id = _uuid_field(payload, "operator_id")
        attributes = dict(payload)
        attributes["status"] = "created"
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("rfq", aggregate_id, attributes),),
            edge_upserts=(
                GraphEdgeUpsert("HAS_RFQ", "mission", mission_id, "rfq", aggregate_id),
                GraphEdgeUpsert("SENT_TO", "rfq", aggregate_id, "operator", operator_id),
            ),
        )

    rfq_statuses = {
        "RFQ_SENT": "sent",
        "RFQ_ACKNOWLEDGED": "acknowledged",
        "RFQ_DECLINED": "declined",
        "RFQ_EXPIRED": "expired",
        "RFQ_QUOTED": "quoted",
    }
    if event_type in rfq_statuses:
        _expect_aggregate(envelope, "rfq")
        attributes = dict(payload)
        attributes["status"] = rfq_statuses[event_type]
        edges: tuple[GraphEdgeUpsert, ...] = ()
        if event_type == "RFQ_QUOTED":
            quote_id = _uuid_field(payload, "quote_id")
            edges = (
                GraphEdgeUpsert(
                    "RECEIVED_QUOTE",
                    "rfq",
                    aggregate_id,
                    "quote",
                    quote_id,
                ),
            )
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("rfq", aggregate_id, attributes),),
            edge_upserts=edges,
        )

    if event_type in {"QUOTE_SUBMITTED", "QUOTE_REVISED"}:
        _expect_aggregate(envelope, "quote")
        rfq_id = _uuid_field(payload, "rfq_id")
        aircraft_id = _uuid_field(payload, "aircraft_id")
        attributes = dict(payload)
        attributes["status"] = "submitted"
        attributes["creation_event"] = event_type
        edges = [
            GraphEdgeUpsert("HAS_QUOTE", "rfq", rfq_id, "quote", aggregate_id),
            GraphEdgeUpsert(
                "PROPOSES_AIRCRAFT",
                "quote",
                aggregate_id,
                "aircraft",
                aircraft_id,
            ),
        ]
        supersedes = _optional_uuid_field(payload, "supersedes_quote_id")
        if supersedes is not None:
            edges.append(
                GraphEdgeUpsert(
                    "SUPERSEDES",
                    "quote",
                    aggregate_id,
                    "quote",
                    supersedes,
                )
            )
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("quote", aggregate_id, attributes),),
            edge_upserts=tuple(edges),
        )

    quote_statuses = {
        "QUOTE_ACCEPTED": "accepted",
        "QUOTE_REJECTED": "rejected",
        "QUOTE_WITHDRAWN": "withdrawn",
        "QUOTE_EXPIRED": "expired",
        "QUOTE_SUPERSEDED": "superseded",
    }
    if event_type in quote_statuses:
        _expect_aggregate(envelope, "quote")
        attributes = dict(payload)
        attributes["status"] = quote_statuses[event_type]
        edges: list[GraphEdgeUpsert] = []
        if event_type == "QUOTE_ACCEPTED":
            booking_id = _uuid_field(payload, "booking_id")
            edges.append(
                GraphEdgeUpsert(
                    "ACCEPTED_AS",
                    "quote",
                    aggregate_id,
                    "booking",
                    booking_id,
                )
            )
        elif event_type == "QUOTE_REJECTED":
            accepted_quote_id = _uuid_field(payload, "accepted_quote_id")
            edges.append(
                GraphEdgeUpsert(
                    "REJECTED_IN_FAVOR_OF",
                    "quote",
                    aggregate_id,
                    "quote",
                    accepted_quote_id,
                )
            )
        elif event_type == "QUOTE_SUPERSEDED":
            replacement = _uuid_field(payload, "replacement_quote_id")
            edges.append(
                GraphEdgeUpsert(
                    "SUPERSEDED_BY",
                    "quote",
                    aggregate_id,
                    "quote",
                    replacement,
                )
            )
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("quote", aggregate_id, attributes),),
            edge_upserts=tuple(edges),
        )

    if event_type == "BOOKING_CREATED":
        _expect_aggregate(envelope, "booking")
        mission_id = _uuid_field(payload, "mission_id")
        quote_id = _uuid_field(payload, "accepted_quote_id")
        operator_id = _uuid_field(payload, "operator_id")
        aircraft_id = _uuid_field(payload, "aircraft_id")
        return GraphMutation(
            node_upserts=(GraphNodeUpsert("booking", aggregate_id, payload),),
            edge_upserts=(
                GraphEdgeUpsert("HAS_BOOKING", "mission", mission_id, "booking", aggregate_id),
                GraphEdgeUpsert("ACCEPTED_QUOTE", "booking", aggregate_id, "quote", quote_id),
                GraphEdgeUpsert("WITH_OPERATOR", "booking", aggregate_id, "operator", operator_id),
                GraphEdgeUpsert("USES_AIRCRAFT", "booking", aggregate_id, "aircraft", aircraft_id),
            ),
        )

    if event_type in {
        "BOOKING_CONTRACTED",
        "BOOKING_PAYMENT_PENDING",
        "BOOKING_CONFIRMED",
        "BOOKING_PRE_OPERATION",
        "BOOKING_OPERATING",
        "BOOKING_COMPLETED",
        "BOOKING_RECONCILED",
    }:
        _expect_aggregate(envelope, "booking")
        to_state = payload.get("to_state")
        if not isinstance(to_state, str) or not to_state:
            raise GraphProjectionConsistencyError(f"{event_type} requires a non-empty to_state")
        attributes = dict(payload)
        attributes["state"] = to_state
        return GraphMutation(node_upserts=(GraphNodeUpsert("booking", aggregate_id, attributes),))

    raise UnsupportedGraphEventError(
        f"no Charter Graph v{CURRENT_PROJECTION_VERSION} mapping for "
        f"{envelope.aggregate_type}:{event_type}"
    )


def reference_state_from(events: tuple[OutboxEnvelope, ...]) -> GraphReferenceState:
    state = GraphReferenceState()
    cursors: dict[tuple[str, UUID], int] = {}
    for envelope in events:
        if not is_projected_aggregate(envelope.aggregate_type):
            continue
        key = (envelope.aggregate_type, envelope.aggregate_id)
        expected = cursors.get(key, 0) + 1
        if envelope.aggregate_version != expected:
            raise GraphProjectionGapError(
                f"reference replay gap for {envelope.aggregate_type}:{envelope.aggregate_id}; "
                f"expected version {expected}, got {envelope.aggregate_version}"
            )
        state.apply(graph_mutation_for(envelope))
        cursors[key] = envelope.aggregate_version
    return state


def _event_payload(envelope: OutboxEnvelope) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(envelope.canonical_json))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GraphProjectionConsistencyError(
            f"event {envelope.event_id} canonical_json is invalid"
        ) from exc
    if not isinstance(raw, dict):
        raise GraphProjectionConsistencyError(
            f"event {envelope.event_id} canonical_json must be an object"
        )
    document = cast(dict[object, object], raw)
    expected_fields: dict[str, object] = {
        "event_id": str(envelope.event_id),
        "aggregate_type": envelope.aggregate_type,
        "aggregate_id": str(envelope.aggregate_id),
        "aggregate_version": envelope.aggregate_version,
        "event_type": envelope.event_type,
        "event_version": envelope.event_version,
    }
    for key, expected in expected_fields.items():
        if document.get(key) != expected:
            raise GraphProjectionConsistencyError(
                f"event {envelope.event_id} canonical envelope mismatch for {key}"
            )
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise GraphProjectionConsistencyError(
            f"event {envelope.event_id} payload must be an object"
        )
    if not all(isinstance(key, str) for key in payload):
        raise GraphProjectionConsistencyError(
            f"event {envelope.event_id} payload keys must be strings"
        )
    return cast(dict[str, object], payload)


def _expect_aggregate(envelope: OutboxEnvelope, expected: str) -> None:
    if envelope.aggregate_type != expected:
        raise GraphProjectionConsistencyError(
            f"event {envelope.event_id} type {envelope.event_type} belongs to "
            f"{envelope.aggregate_type}, expected {expected}"
        )


def _uuid_field(payload: Mapping[str, object], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise GraphProjectionConsistencyError(f"{key} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise GraphProjectionConsistencyError(f"{key} must be a valid UUID") from exc


def _optional_uuid_field(payload: Mapping[str, object], key: str) -> UUID | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphProjectionConsistencyError(f"{key} must be null or a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise GraphProjectionConsistencyError(f"{key} must be a valid UUID") from exc
