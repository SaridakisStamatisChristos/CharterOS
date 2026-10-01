from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class OrphanReferenceFinding:
    constraint_name: str
    child_table: str
    parent_table: str
    orphan_count: int


@dataclass(frozen=True, slots=True)
class OutboxRecoveryState:
    counts_by_status: dict[str, int]
    consumer_receipt_count: int
    expired_in_flight_count: int
    poisoned_event_count: int


@dataclass(frozen=True, slots=True)
class RecoveryDatabaseSnapshot:
    schema_revision: str | None
    latest_canonical_event_at: datetime | None
    projected_event_count: int
    evidence_violation_count: int
    outbox: OutboxRecoveryState
    capacity_overlap_count: int
    orphan_references: tuple[OrphanReferenceFinding, ...]
    future_timestamp_count: int
    timestamp_future_tolerance_seconds: int


@dataclass(frozen=True, slots=True)
class GraphRecoveryState:
    projection_version: int | None
    rebuild_version: int | None
    ok: bool
    event_count: int
    reference_digest: str | None
    persisted_digest: str | None
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    schema_version: str
    database_id: str
    environment: str
    expected_schema_revision: str
    restored_schema_revision: str | None
    schema_revision_ok: bool
    backup_cutoff_at: datetime | None
    latest_canonical_event_at: datetime | None
    expected_latest_canonical_at: datetime | None
    graph: GraphRecoveryState
    evidence_integrity_ok: bool
    evidence_violation_count: int
    outbox: OutboxRecoveryState
    capacity_overlap_ok: bool
    capacity_overlap_count: int
    orphan_references: tuple[OrphanReferenceFinding, ...]
    future_timestamp_count: int
    recovery_started_at: datetime
    recovery_completed_at: datetime
    rto_seconds: float
    rto_scope: str
    observed_data_loss_seconds: float | None
    rpo_evidence_basis: str
    verification_read_only: bool
    ok: bool
    issues: tuple[str, ...]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recovery timestamps must be timezone-aware")
    return value.astimezone(UTC)


def observed_data_loss_seconds(
    *,
    expected_latest_canonical_at: datetime | None,
    restored_latest_canonical_at: datetime | None,
) -> float | None:
    if expected_latest_canonical_at is None or restored_latest_canonical_at is None:
        return None
    expected = utc(expected_latest_canonical_at)
    restored = utc(restored_latest_canonical_at)
    return max(0.0, (expected - restored).total_seconds())


def elapsed_seconds(*, started_at: datetime, completed_at: datetime) -> float:
    started = utc(started_at)
    completed = utc(completed_at)
    if completed < started:
        raise ValueError("recovery completion cannot precede recovery start")
    return (completed - started).total_seconds()
