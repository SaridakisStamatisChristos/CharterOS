from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class DecisionEvidenceSnapshotRow(Base):
    __tablename__ = "decision_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "decision_type",
            "source_aggregate_type",
            "source_aggregate_id",
            name="uq_decision_evidence_source",
        ),
        CheckConstraint(
            "char_length(decision_type) > 0",
            name="ck_decision_evidence_decision_type",
        ),
        CheckConstraint(
            "char_length(subject_type) > 0",
            name="ck_decision_evidence_subject_type",
        ),
        CheckConstraint(
            "char_length(schema_version) > 0",
            name="ck_decision_evidence_schema_version",
        ),
        CheckConstraint(
            "char_length(integrity_digest) = 64",
            name="ck_decision_evidence_digest_length",
        ),
        Index(
            "ix_decision_evidence_subject_time",
            "subject_type",
            "subject_id",
            "decided_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    known_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    policy_versions: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    canonical_json: Mapped[str] = mapped_column(Text, nullable=False)
    integrity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
