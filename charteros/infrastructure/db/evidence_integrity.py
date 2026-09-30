from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.infrastructure.db.models.evidence_integrity import (
    EvidenceIntegrityCheckpointRow,
    EvidenceIntegrityEntryRow,
)


@dataclass(frozen=True, slots=True)
class EvidenceIntegrityViolation:
    stream_key: str
    sequence: int
    source_table: str
    source_key: dict[str, object]
    violation: str


@dataclass(frozen=True, slots=True)
class EvidenceIntegrityCheckpoint:
    id: UUID
    stream_key: str
    through_sequence: int
    root_digest: str


def verify_evidence_integrity(
    session: Session,
    *,
    stream_key: str | None = None,
) -> tuple[EvidenceIntegrityViolation, ...]:
    rows = session.execute(
        text(
            "SELECT stream_key, sequence, source_table, source_key, violation "
            "FROM charteros_verify_evidence_integrity(:stream_key)"
        ),
        {"stream_key": stream_key},
    ).mappings()
    return tuple(
        EvidenceIntegrityViolation(
            stream_key=str(row["stream_key"]),
            sequence=int(row["sequence"]),
            source_table=str(row["source_table"]),
            source_key=dict(row["source_key"]),
            violation=str(row["violation"]),
        )
        for row in rows
    )


def assert_evidence_integrity(
    session: Session,
    *,
    stream_key: str | None = None,
) -> None:
    violations = verify_evidence_integrity(session, stream_key=stream_key)
    if not violations:
        return
    first = violations[0]
    raise EntityConflictError(
        "database evidence integrity violation: "
        f"{first.violation} in {first.source_table} at "
        f"{first.stream_key}#{first.sequence}"
    )


def create_evidence_checkpoint(
    session: Session,
    *,
    stream_key: str,
) -> EvidenceIntegrityCheckpoint:
    latest = session.scalar(
        select(EvidenceIntegrityEntryRow)
        .where(EvidenceIntegrityEntryRow.stream_key == stream_key)
        .order_by(EvidenceIntegrityEntryRow.sequence.desc())
        .limit(1)
    )
    if latest is None:
        raise EntityConflictError(f"cannot checkpoint empty evidence stream {stream_key}")

    existing = session.scalar(
        select(EvidenceIntegrityCheckpointRow).where(
            EvidenceIntegrityCheckpointRow.stream_key == stream_key,
            EvidenceIntegrityCheckpointRow.through_sequence == latest.sequence,
        )
    )
    if existing is None:
        existing = EvidenceIntegrityCheckpointRow(
            id=uuid4(),
            stream_key=stream_key,
            through_sequence=latest.sequence,
            root_digest=latest.digest,
        )
        session.add(existing)
        session.flush()

    return EvidenceIntegrityCheckpoint(
        id=existing.id,
        stream_key=existing.stream_key,
        through_sequence=existing.through_sequence,
        root_digest=existing.root_digest,
    )
