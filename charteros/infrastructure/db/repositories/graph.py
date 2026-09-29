from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.graph_projection import (
    PROJECTION_NAME,
    GraphEdgeUpsert,
    GraphNodeUpsert,
    GraphProjectionConsistencyError,
    GraphProjectionGapError,
    consumer_name,
    graph_mutation_for,
    is_projected_aggregate,
)
from charteros.application.outbox import OutboxEnvelope
from charteros.infrastructure.db.models.graph import (
    GraphAggregateCursorRow,
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
    GraphProjectionVersionRow,
)
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.graph_verification import (
    GraphVerificationReport,
    historical_graph_envelopes,
    verify_graph_projection,
)
from charteros.infrastructure.db.repositories.outbox import SqlAlchemyIdempotentConsumerRunner


@dataclass(frozen=True, slots=True)
class GraphProjectionStatus:
    projection_name: str
    projection_version: int
    status: str
    event_count: int
    state_digest: str | None
    created_at: datetime
    verified_at: datetime | None
    activated_at: datetime | None


class SqlAlchemyGraphProjectionStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._runner = SqlAlchemyIdempotentConsumerRunner(session_factory)

    def active_version(self) -> int | None:
        with self._session_factory() as session:
            return session.scalar(
                select(GraphProjectionVersionRow.projection_version).where(
                    GraphProjectionVersionRow.projection_name == PROJECTION_NAME,
                    GraphProjectionVersionRow.status == "active",
                )
            )

    def statuses(self) -> tuple[GraphProjectionStatus, ...]:
        with self._session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(GraphProjectionVersionRow)
                    .where(GraphProjectionVersionRow.projection_name == PROJECTION_NAME)
                    .order_by(GraphProjectionVersionRow.projection_version)
                )
            )
        return tuple(
            GraphProjectionStatus(
                projection_name=row.projection_name,
                projection_version=row.projection_version,
                status=row.status,
                event_count=row.event_count,
                state_digest=row.state_digest,
                created_at=row.created_at,
                verified_at=row.verified_at,
                activated_at=row.activated_at,
            )
            for row in rows
        )

    def prepare_version(
        self, projection_version: int, *, now: datetime, reset_building: bool = False
    ) -> None:
        if projection_version < 1:
            raise ValueError("projection_version must be positive")
        when = _utc(now)
        with self._session_factory.begin() as session:
            _advisory_lock(session, f"{PROJECTION_NAME}:version:{projection_version}")
            row = session.get(GraphProjectionVersionRow, (PROJECTION_NAME, projection_version))
            if row is None:
                session.add(
                    GraphProjectionVersionRow(
                        projection_name=PROJECTION_NAME,
                        projection_version=projection_version,
                        status="building",
                        active_key=None,
                        created_at=when,
                        verified_at=None,
                        activated_at=None,
                        state_digest=None,
                        event_count=0,
                    )
                )
                session.add(
                    GraphProjectionCheckpointRow(
                        projection_name=PROJECTION_NAME,
                        projection_version=projection_version,
                        processed_event_count=0,
                        max_recorded_at=None,
                        max_recorded_event_id=None,
                        updated_at=when,
                    )
                )
                session.flush()
                return
            if row.status != "building":
                raise GraphProjectionConsistencyError(
                    f"projection v{projection_version} is {row.status}; only building versions "
                    "can be resumed or reset"
                )
            if reset_building:
                self._reset_building(session, projection_version, when=when)

    def rebuild(
        self, projection_version: int, *, now: datetime, reset_building: bool = False
    ) -> GraphVerificationReport:
        when = _utc(now)
        self.prepare_version(projection_version, now=when, reset_building=reset_building)
        for envelope in historical_graph_envelopes(self._session_factory):
            self.consume_into_version(
                projection_version, envelope, processed_at=envelope.recorded_at
            )
        report = self.verify(projection_version)
        if report.ok:
            with self._session_factory.begin() as session:
                _advisory_lock(session, f"{PROJECTION_NAME}:version:{projection_version}")
                row = session.get(GraphProjectionVersionRow, (PROJECTION_NAME, projection_version))
                if row is None or row.status != "building":
                    raise GraphProjectionConsistencyError(
                        f"projection v{projection_version} changed state during rebuild"
                    )
                row.status = "verified"
                row.state_digest = report.persisted_digest
                row.event_count = report.event_count
                row.verified_at = when
        return report

    def verify(self, projection_version: int) -> GraphVerificationReport:
        return verify_graph_projection(self._session_factory, projection_version)

    def activate(self, projection_version: int, *, now: datetime, maintenance_mode: bool) -> None:
        if not maintenance_mode:
            raise GraphProjectionConsistencyError(
                "activation requires maintenance_mode=True so writes/workers are quiesced"
            )
        report = self.verify(projection_version)
        if not report.ok:
            raise GraphProjectionConsistencyError(
                f"projection v{projection_version} cannot activate: " + "; ".join(report.issues)
            )
        when = _utc(now)
        with self._session_factory.begin() as session:
            _advisory_lock(session, f"{PROJECTION_NAME}:activation")
            target = session.scalar(
                select(GraphProjectionVersionRow)
                .where(
                    GraphProjectionVersionRow.projection_name == PROJECTION_NAME,
                    GraphProjectionVersionRow.projection_version == projection_version,
                )
                .with_for_update()
            )
            if target is None or target.status not in {"verified", "active"}:
                raise GraphProjectionConsistencyError(
                    f"projection v{projection_version} must be verified before activation"
                )
            if target.status == "active":
                return
            active_rows = tuple(
                session.scalars(
                    select(GraphProjectionVersionRow)
                    .where(
                        GraphProjectionVersionRow.projection_name == PROJECTION_NAME,
                        GraphProjectionVersionRow.status == "active",
                    )
                    .with_for_update()
                )
            )
            if len(active_rows) > 1:
                raise GraphProjectionConsistencyError("multiple active Charter Graph versions")
            for active in active_rows:
                active.status = "retired"
                active.active_key = None
            target.status = "active"
            target.active_key = "active"
            target.activated_at = when
            target.state_digest = report.persisted_digest
            target.event_count = report.event_count

    def consume_active(self, envelope: OutboxEnvelope, *, processed_at: datetime) -> bool:
        if not is_projected_aggregate(envelope.aggregate_type):
            return False
        version = self.active_version()
        if version is None:
            raise GraphProjectionConsistencyError(
                "no active Charter Graph projection; rebuild and activate before consumption"
            )
        return self.consume_into_version(version, envelope, processed_at=processed_at)

    def consume_into_version(
        self, projection_version: int, envelope: OutboxEnvelope, *, processed_at: datetime
    ) -> bool:
        if not is_projected_aggregate(envelope.aggregate_type):
            return False
        when = _utc(processed_at)

        def handler(session: Session, item: OutboxEnvelope) -> None:
            self._apply(session, projection_version, item, processed_at=when)

        return self._runner.consume(
            consumer_name=consumer_name(projection_version),
            consumer_version=projection_version,
            envelope=envelope,
            processed_at=when,
            handler=handler,
        )

    def _apply(
        self,
        session: Session,
        projection_version: int,
        envelope: OutboxEnvelope,
        *,
        processed_at: datetime,
    ) -> None:
        version = session.scalar(
            select(GraphProjectionVersionRow)
            .where(
                GraphProjectionVersionRow.projection_name == PROJECTION_NAME,
                GraphProjectionVersionRow.projection_version == projection_version,
            )
            .with_for_update()
        )
        if version is None or version.status not in {"building", "active"}:
            state = "missing" if version is None else version.status
            raise GraphProjectionConsistencyError(
                f"projection v{projection_version} is {state}; cannot consume events"
            )
        _advisory_lock(
            session,
            f"{PROJECTION_NAME}:v{projection_version}:"
            f"{envelope.aggregate_type}:{envelope.aggregate_id}",
        )
        cursor = session.scalar(
            select(GraphAggregateCursorRow)
            .where(
                GraphAggregateCursorRow.projection_name == PROJECTION_NAME,
                GraphAggregateCursorRow.projection_version == projection_version,
                GraphAggregateCursorRow.aggregate_type == envelope.aggregate_type,
                GraphAggregateCursorRow.aggregate_id == envelope.aggregate_id,
            )
            .with_for_update()
        )
        expected = 1 if cursor is None else cursor.last_aggregate_version + 1
        if envelope.aggregate_version != expected:
            if cursor is not None and envelope.aggregate_version <= cursor.last_aggregate_version:
                raise GraphProjectionConsistencyError(
                    "aggregate version already passed without matching consumer receipt"
                )
            raise GraphProjectionGapError(
                f"projection v{projection_version} gap for "
                f"{envelope.aggregate_type}:{envelope.aggregate_id}; expected version "
                f"{expected}, got {envelope.aggregate_version}"
            )

        mutation = graph_mutation_for(envelope)
        for item in mutation.node_upserts:
            self._upsert_node(session, projection_version, envelope, item, processed_at)
        for item in mutation.edge_upserts:
            self._upsert_edge(session, projection_version, envelope, item, processed_at)

        if cursor is None:
            session.add(
                GraphAggregateCursorRow(
                    projection_name=PROJECTION_NAME,
                    projection_version=projection_version,
                    aggregate_type=envelope.aggregate_type,
                    aggregate_id=envelope.aggregate_id,
                    last_aggregate_version=envelope.aggregate_version,
                    last_event_id=envelope.event_id,
                    updated_at=processed_at,
                )
            )
        else:
            cursor.last_aggregate_version = envelope.aggregate_version
            cursor.last_event_id = envelope.event_id
            cursor.updated_at = processed_at
        checkpoint = session.scalar(
            select(GraphProjectionCheckpointRow)
            .where(
                GraphProjectionCheckpointRow.projection_name == PROJECTION_NAME,
                GraphProjectionCheckpointRow.projection_version == projection_version,
            )
            .with_for_update()
        )
        if checkpoint is None:
            raise GraphProjectionConsistencyError(
                f"projection v{projection_version} has no durable checkpoint"
            )
        checkpoint.processed_event_count += 1
        if _later_than_checkpoint(checkpoint, envelope):
            checkpoint.max_recorded_at = envelope.recorded_at
            checkpoint.max_recorded_event_id = envelope.event_id
        checkpoint.updated_at = processed_at
        session.flush()

    def _upsert_node(
        self,
        session: Session,
        version: int,
        envelope: OutboxEnvelope,
        item: GraphNodeUpsert,
        processed_at: datetime,
    ) -> None:
        row = session.get(GraphNodeRow, (PROJECTION_NAME, version, item.node_type, item.node_id))
        values = dict(item.attributes)
        if row is None:
            session.add(
                GraphNodeRow(
                    projection_name=PROJECTION_NAME,
                    projection_version=version,
                    node_type=item.node_type,
                    node_id=item.node_id,
                    attributes=values,
                    source_aggregate_type=envelope.aggregate_type,
                    source_aggregate_id=envelope.aggregate_id,
                    source_aggregate_version=envelope.aggregate_version,
                    last_event_id=envelope.event_id,
                    updated_at=processed_at,
                )
            )
            return
        row.attributes = _merged(row.attributes, values)
        _stamp(row, envelope, processed_at)

    def _upsert_edge(
        self,
        session: Session,
        version: int,
        envelope: OutboxEnvelope,
        item: GraphEdgeUpsert,
        processed_at: datetime,
    ) -> None:
        key = (
            PROJECTION_NAME,
            version,
            item.edge_type,
            item.source_type,
            item.source_id,
            item.target_type,
            item.target_id,
        )
        row = session.get(GraphEdgeRow, key)
        values = dict(item.attributes)
        if row is None:
            session.add(
                GraphEdgeRow(
                    projection_name=PROJECTION_NAME,
                    projection_version=version,
                    edge_type=item.edge_type,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    attributes=values,
                    source_aggregate_type=envelope.aggregate_type,
                    source_aggregate_id=envelope.aggregate_id,
                    source_aggregate_version=envelope.aggregate_version,
                    last_event_id=envelope.event_id,
                    updated_at=processed_at,
                )
            )
            return
        row.attributes = _merged(row.attributes, values)
        _stamp(row, envelope, processed_at)

    def _reset_building(self, session: Session, projection_version: int, *, when: datetime) -> None:
        session.execute(
            delete(GraphEdgeRow).where(
                GraphEdgeRow.projection_name == PROJECTION_NAME,
                GraphEdgeRow.projection_version == projection_version,
            )
        )
        session.execute(
            delete(GraphNodeRow).where(
                GraphNodeRow.projection_name == PROJECTION_NAME,
                GraphNodeRow.projection_version == projection_version,
            )
        )
        session.execute(
            delete(GraphAggregateCursorRow).where(
                GraphAggregateCursorRow.projection_name == PROJECTION_NAME,
                GraphAggregateCursorRow.projection_version == projection_version,
            )
        )
        session.execute(
            delete(OutboxConsumerReceiptRow).where(
                OutboxConsumerReceiptRow.consumer_name == consumer_name(projection_version)
            )
        )
        checkpoint = session.get(
            GraphProjectionCheckpointRow, (PROJECTION_NAME, projection_version)
        )
        if checkpoint is None:
            raise GraphProjectionConsistencyError("building projection checkpoint is missing")
        checkpoint.processed_event_count = 0
        checkpoint.max_recorded_at = None
        checkpoint.max_recorded_event_id = None
        checkpoint.updated_at = when
        version = session.get(GraphProjectionVersionRow, (PROJECTION_NAME, projection_version))
        if version is None or version.status != "building":
            raise GraphProjectionConsistencyError("only a building projection can be reset")
        version.state_digest = None
        version.event_count = 0
        version.verified_at = None


def _merged(current: Mapping[str, object], values: Mapping[str, object]) -> dict[str, object]:
    result = dict(current)
    result.update(values)
    return result


def _stamp(row: GraphNodeRow | GraphEdgeRow, envelope: OutboxEnvelope, when: datetime) -> None:
    row.source_aggregate_type = envelope.aggregate_type
    row.source_aggregate_id = envelope.aggregate_id
    row.source_aggregate_version = envelope.aggregate_version
    row.last_event_id = envelope.event_id
    row.updated_at = when


def _later_than_checkpoint(
    checkpoint: GraphProjectionCheckpointRow, envelope: OutboxEnvelope
) -> bool:
    return (
        checkpoint.max_recorded_at is None
        or envelope.recorded_at > checkpoint.max_recorded_at
        or (
            envelope.recorded_at == checkpoint.max_recorded_at
            and (
                checkpoint.max_recorded_event_id is None
                or str(envelope.event_id) > str(checkpoint.max_recorded_event_id)
            )
        )
    )


def _advisory_lock(session: Session, key: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:graph_lock_key, 0))"),
            {"graph_lock_key": key},
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("graph projection timestamps must be timezone-aware")
    return value.astimezone(UTC)
