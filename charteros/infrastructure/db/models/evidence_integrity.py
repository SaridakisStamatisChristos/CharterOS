from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class EvidenceIntegrityEntryRow(Base):
    __tablename__ = "evidence_integrity_entries"
    __table_args__ = (
        UniqueConstraint(
            "source_table",
            "source_key",
            name="uq_evidence_integrity_source",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_evidence_integrity_sequence_positive",
        ),
        CheckConstraint(
            "previous_digest IS NULL OR char_length(previous_digest) = 64",
            name="ck_evidence_integrity_previous_digest",
        ),
        CheckConstraint(
            "char_length(digest) = 64",
            name="ck_evidence_integrity_digest",
        ),
        Index("ix_evidence_integrity_source", "source_table"),
    )

    stream_key: Mapped[str] = mapped_column(Text, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    canonical_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    previous_digest: Mapped[str | None] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )


class EvidenceIntegrityCheckpointRow(Base):
    __tablename__ = "evidence_integrity_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stream_key", "through_sequence"],
            [
                "evidence_integrity_entries.stream_key",
                "evidence_integrity_entries.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_evidence_checkpoint_entry",
        ),
        UniqueConstraint(
            "stream_key",
            "through_sequence",
            name="uq_evidence_checkpoint_stream_sequence",
        ),
        CheckConstraint(
            "through_sequence > 0",
            name="ck_evidence_checkpoint_sequence_positive",
        ),
        CheckConstraint(
            "char_length(root_digest) = 64",
            name="ck_evidence_checkpoint_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stream_key: Mapped[str] = mapped_column(Text, nullable=False)
    through_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    root_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
