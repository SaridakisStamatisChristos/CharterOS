from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.graph_projection import (
    PROJECTION_NAME,
    SUPPORTED_AGGREGATE_TYPES,
    GraphProjectionGapError,
    GraphReferenceState,
    consumer_name,
    reference_state_from,
)
from charteros.application.outbox import OutboxEnvelope
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphAggregateCursorRow,
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
    GraphProjectionVersionRow,
)
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow


@dataclass(frozen=True, slots=True)
class GraphVerificationReport:
    projection_name: str
    projection_version: int
    ok: bool
    event_count: int
    receipt_count: int
    checkpoint_count: int
    node_count: int
    edge_count: int
    reference_digest: str | None
    persisted_digest: str | None
    issues: tuple[str, ...]


def verify_graph_projection(
    session_factory: sessionmaker[Session], projection_version: int
) -> GraphVerificationReport:
    issues: list[str] = []
    with session_factory() as session:
        version = session.get(GraphProjectionVersionRow, (PROJECTION_NAME, projection_version))
        if version is None:
            return _missing_report(projection_version)

    events = historical_graph_envelopes(session_factory)
    reference: GraphReferenceState | None = None
    reference_digest: str | None = None
    try:
        reference = reference_state_from(events)
        reference_digest = reference.digest()
    except GraphProjectionGapError as exc:
        issues.append(str(exc))

    with session_factory() as session:
        nodes = tuple(
            session.scalars(
                select(GraphNodeRow).where(
                    GraphNodeRow.projection_name == PROJECTION_NAME,
                    GraphNodeRow.projection_version == projection_version,
                )
            )
        )
        edges = tuple(
            session.scalars(
                select(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == projection_version,
                )
            )
        )
        cursors = tuple(
            session.scalars(
                select(GraphAggregateCursorRow).where(
                    GraphAggregateCursorRow.projection_name == PROJECTION_NAME,
                    GraphAggregateCursorRow.projection_version == projection_version,
                )
            )
        )
        checkpoint = session.get(
            GraphProjectionCheckpointRow, (PROJECTION_NAME, projection_version)
        )
        receipt_count = session.scalar(
            select(func.count())
            .select_from(OutboxConsumerReceiptRow)
            .where(OutboxConsumerReceiptRow.consumer_name == consumer_name(projection_version))
        )
        current = session.get(GraphProjectionVersionRow, (PROJECTION_NAME, projection_version))
        assert current is not None
        stored_digest = current.state_digest
        checkpoint_count = checkpoint.processed_event_count if checkpoint else -1

    event_count = len(events)
    receipt_total = int(receipt_count or 0)
    if checkpoint is None:
        issues.append("durable projection checkpoint is missing")
    if receipt_total != event_count:
        issues.append(f"consumer receipts {receipt_total} != event history {event_count}")
    if checkpoint_count != event_count:
        issues.append(f"checkpoint count {checkpoint_count} != event history {event_count}")

    expected_cursors: dict[tuple[str, UUID], int] = {}
    for envelope in events:
        expected_cursors[(envelope.aggregate_type, envelope.aggregate_id)] = (
            envelope.aggregate_version
        )
    actual_cursors = {
        (row.aggregate_type, row.aggregate_id): row.last_aggregate_version for row in cursors
    }
    if actual_cursors != expected_cursors:
        issues.append("aggregate-version cursors do not match authoritative event history")
    if sum(actual_cursors.values()) != event_count:
        issues.append("aggregate cursor version sum does not match event history")

    persisted = GraphReferenceState()
    for node_row in nodes:
        persisted.nodes[(node_row.node_type, node_row.node_id)] = dict(node_row.attributes)
    for edge_row in edges:
        persisted.edges[
            (
                edge_row.edge_type,
                edge_row.source_type,
                edge_row.source_id,
                edge_row.target_type,
                edge_row.target_id,
            )
        ] = dict(edge_row.attributes)
    persisted_digest = persisted.digest()

    if reference is not None:
        _compare_reference(reference, persisted, issues)
        if reference_digest != persisted_digest:
            issues.append("deterministic reference rebuild digest mismatch")

    node_keys = set(persisted.nodes)
    invalid = [
        key
        for key in persisted.edges
        if (key[1], key[2]) not in node_keys or (key[3], key[4]) not in node_keys
    ]
    if invalid:
        issues.append(f"invalid edge references: {len(invalid)}")
    if stored_digest is not None and stored_digest != persisted_digest:
        issues.append("persisted graph digest differs from last verified digest")

    return GraphVerificationReport(
        projection_name=PROJECTION_NAME,
        projection_version=projection_version,
        ok=not issues,
        event_count=event_count,
        receipt_count=receipt_total,
        checkpoint_count=max(checkpoint_count, 0),
        node_count=len(nodes),
        edge_count=len(edges),
        reference_digest=reference_digest,
        persisted_digest=persisted_digest,
        issues=tuple(issues),
    )


def historical_graph_envelopes(
    session_factory: sessionmaker[Session],
) -> tuple[OutboxEnvelope, ...]:
    with session_factory() as session:
        rows = tuple(
            session.scalars(
                select(OutboxEventRow)
                .where(OutboxEventRow.aggregate_type.in_(tuple(sorted(SUPPORTED_AGGREGATE_TYPES))))
                .order_by(
                    OutboxEventRow.aggregate_type,
                    OutboxEventRow.aggregate_id,
                    OutboxEventRow.aggregate_version,
                    OutboxEventRow.event_id,
                )
            )
        )
        return tuple(_to_envelope(row) for row in rows)


def _compare_reference(
    reference: GraphReferenceState, persisted: GraphReferenceState, issues: list[str]
) -> None:
    checks = (
        ("missing projected nodes", set(reference.nodes) - set(persisted.nodes)),
        ("unexpected projected nodes", set(persisted.nodes) - set(reference.nodes)),
        ("missing projected edges", set(reference.edges) - set(persisted.edges)),
        ("unexpected projected edges", set(persisted.edges) - set(reference.edges)),
    )
    for label, values in checks:
        if values:
            issues.append(f"{label}: {len(values)}")


def _missing_report(projection_version: int) -> GraphVerificationReport:
    return GraphVerificationReport(
        projection_name=PROJECTION_NAME,
        projection_version=projection_version,
        ok=False,
        event_count=0,
        receipt_count=0,
        checkpoint_count=0,
        node_count=0,
        edge_count=0,
        reference_digest=None,
        persisted_digest=None,
        issues=("projection version does not exist",),
    )


def _to_envelope(row: OutboxEventRow) -> OutboxEnvelope:
    return OutboxEnvelope(
        event_id=row.event_id,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        event_type=row.event_type,
        event_version=row.event_version,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        actor_id=row.actor_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        canonical_json=row.canonical_json,
    )
