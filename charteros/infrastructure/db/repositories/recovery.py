from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.orm import Session

from charteros.application.graph_projection import SUPPORTED_AGGREGATE_TYPES
from charteros.application.outbox import OutboxDeliveryStatus
from charteros.application.recovery import (
    OrphanReferenceFinding,
    OutboxRecoveryState,
    RecoveryDatabaseSnapshot,
    utc,
)
from charteros.infrastructure.db.evidence_integrity import verify_evidence_integrity
from charteros.infrastructure.db.models.capacity import AircraftCapacityReservationRow
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.evidence_integrity import EvidenceIntegrityEntryRow
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow

_DEFAULT_FUTURE_TOLERANCE_SECONDS = 300


class SqlAlchemyRecoveryVerificationRepository:
    """Read-only verification of canonical PostgreSQL state after a restore."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def snapshot(
        self,
        *,
        verification_time: datetime,
        future_tolerance_seconds: int = _DEFAULT_FUTURE_TOLERANCE_SECONDS,
    ) -> RecoveryDatabaseSnapshot:
        when = utc(verification_time)
        if future_tolerance_seconds < 0:
            raise ValueError("future_tolerance_seconds cannot be negative")
        if self._session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("recovery verification requires PostgreSQL")

        # This must be the first SQL statement in the transaction. Verification is deliberately
        # incapable of repairing state or silently rewriting restored evidence.
        self._session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )

        schema_revision = self._session.scalar(text("SELECT version_num FROM alembic_version"))
        latest_canonical_event_at = self._session.scalar(
            select(func.max(OutboxEventRow.recorded_at))
        )
        projected_event_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.aggregate_type.in_(
                        tuple(sorted(SUPPORTED_AGGREGATE_TYPES))
                    )
                )
            )
            or 0
        )

        evidence_violations = verify_evidence_integrity(self._session)
        outbox = self._outbox_state(when=when)
        capacity_overlap_count = self._capacity_overlap_count()
        orphan_references = self._orphan_references()
        future_timestamp_count = self._future_timestamp_count(
            when=when,
            tolerance_seconds=future_tolerance_seconds,
        )

        return RecoveryDatabaseSnapshot(
            schema_revision=str(schema_revision) if schema_revision is not None else None,
            latest_canonical_event_at=latest_canonical_event_at,
            projected_event_count=projected_event_count,
            evidence_violation_count=len(evidence_violations),
            outbox=outbox,
            capacity_overlap_count=capacity_overlap_count,
            orphan_references=orphan_references,
            future_timestamp_count=future_timestamp_count,
            timestamp_future_tolerance_seconds=future_tolerance_seconds,
        )

    def _outbox_state(self, *, when: datetime) -> OutboxRecoveryState:
        rows = self._session.execute(
            select(OutboxEventRow.delivery_status, func.count())
            .group_by(OutboxEventRow.delivery_status)
            .order_by(OutboxEventRow.delivery_status)
        ).all()
        counts: dict[str, int] = {
            status.value: 0
            for status in OutboxDeliveryStatus
        }
        for status, count in rows:
            counts[str(status)] = int(count)

        receipt_count = int(
            self._session.scalar(select(func.count()).select_from(OutboxConsumerReceiptRow)) or 0
        )
        expired_in_flight_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.delivery_status == OutboxDeliveryStatus.IN_FLIGHT.value,
                    OutboxEventRow.lease_expires_at <= when,
                )
            )
            or 0
        )
        return OutboxRecoveryState(
            counts_by_status=counts,
            consumer_receipt_count=receipt_count,
            expired_in_flight_count=expired_in_flight_count,
            poisoned_event_count=counts[OutboxDeliveryStatus.POISONED.value],
        )

    def _capacity_overlap_count(self) -> int:
        left = AircraftCapacityReservationRow.__table__.alias("left_reservation")
        right = AircraftCapacityReservationRow.__table__.alias("right_reservation")
        value = self._session.scalar(
            select(func.count())
            .select_from(
                left.join(
                    right,
                    (left.c.aircraft_id == right.c.aircraft_id)
                    & (left.c.id < right.c.id)
                    & left.c.occupied_range.op("&&")(right.c.occupied_range),
                )
            )
            .where(
                left.c.status == "reserved",
                right.c.status == "reserved",
            )
        )
        return int(value or 0)

    def _future_timestamp_count(self, *, when: datetime, tolerance_seconds: int) -> int:
        threshold = when + timedelta(seconds=tolerance_seconds)
        outbox_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    or_(
                        OutboxEventRow.recorded_at > threshold,
                        OutboxEventRow.occurred_at > threshold,
                    )
                )
            )
            or 0
        )
        evidence_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(EvidenceIntegrityEntryRow)
                .where(EvidenceIntegrityEntryRow.created_at > threshold)
            )
            or 0
        )
        return outbox_count + evidence_count

    def _orphan_references(self) -> tuple[OrphanReferenceFinding, ...]:
        connection = self._session.connection()
        inspector: Inspector = inspect(connection)
        foreign_keys: list[tuple[str, dict[str, Any]]] = []
        for table_name in sorted(inspector.get_table_names(schema="public")):
            reflected = cast(
                list[dict[str, Any]],
                inspector.get_foreign_keys(table_name, schema="public"),
            )
            foreign_keys.extend((table_name, fk) for fk in reflected)

        preparer = connection.dialect.identifier_preparer
        findings: list[OrphanReferenceFinding] = []
        for child_table, foreign_key in foreign_keys:
            child_columns = cast(list[str], foreign_key["constrained_columns"])
            parent_columns = cast(list[str], foreign_key["referred_columns"])
            parent_table = cast(str, foreign_key["referred_table"])
            parent_schema = cast(str | None, foreign_key.get("referred_schema")) or "public"
            if not child_columns or len(child_columns) != len(parent_columns):
                raise RuntimeError(
                    f"cannot verify malformed foreign key on {child_table}: {foreign_key!r}"
                )

            quote = preparer.quote_identifier
            child_ref = f'{quote("public")}.{quote(child_table)}'
            parent_ref = f"{quote(parent_schema)}.{quote(parent_table)}"
            join_predicate = " AND ".join(
                f"c.{quote(child)} = p.{quote(parent)}"
                for child, parent in zip(child_columns, parent_columns, strict=True)
            )
            constrained_values_present = " AND ".join(
                f"c.{quote(child)} IS NOT NULL" for child in child_columns
            )
            parent_missing = f"p.{quote(parent_columns[0])} IS NULL"
            orphan_count = int(
                self._session.scalar(
                    text(
                        f"SELECT count(*) FROM {child_ref} AS c "
                        f"LEFT JOIN {parent_ref} AS p ON {join_predicate} "
                        f"WHERE {constrained_values_present} AND {parent_missing}"
                    )
                )
                or 0
            )
            if orphan_count:
                constraint_name = cast(str | None, foreign_key.get("name"))
                findings.append(
                    OrphanReferenceFinding(
                        constraint_name=constraint_name or "<unnamed>",
                        child_table=child_table,
                        parent_table=parent_table,
                        orphan_count=orphan_count,
                    )
                )

        return tuple(
            sorted(
                findings,
                key=lambda item: (
                    item.child_table,
                    item.constraint_name,
                    item.parent_table,
                ),
            )
        )
