from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class FxRateRow(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        CheckConstraint("version = 1", name="ck_fx_rates_single_event_version"),
        CheckConstraint("revision_number >= 1", name="ck_fx_rates_revision_positive"),
        CheckConstraint(
            "source_currency <> target_currency",
            name="ck_fx_rates_distinct_currencies",
        ),
        CheckConstraint(
            "source_minor_exponent BETWEEN 0 AND 9",
            name="ck_fx_rates_source_minor_exponent",
        ),
        CheckConstraint(
            "target_minor_exponent BETWEEN 0 AND 9",
            name="ck_fx_rates_target_minor_exponent",
        ),
        CheckConstraint("fx_timestamp <= recorded_at", name="ck_fx_rates_temporal_order"),
        UniqueConstraint(
            "fx_source",
            "source_currency",
            "target_currency",
            "fx_timestamp",
            "revision_number",
            name="uq_fx_rates_observation_revision",
        ),
        Index(
            "ix_fx_rates_resolver",
            "fx_source",
            "source_currency",
            "target_currency",
            "fx_timestamp",
            "recorded_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_text: Mapped[str] = mapped_column(String(80), nullable=False)
    source_minor_exponent: Mapped[int] = mapped_column(Integer, nullable=False)
    target_minor_exponent: Mapped[int] = mapped_column(Integer, nullable=False)
    fx_source: Mapped[str] = mapped_column(String(64), nullable=False)
    fx_source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    fx_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_rate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fx_rates.id", ondelete="RESTRICT"),
        unique=True,
    )


class FxLockRow(Base):
    __tablename__ = "fx_locks"
    __table_args__ = (
        CheckConstraint("version IN (1, 2)", name="ck_fx_locks_version"),
        CheckConstraint("status IN ('active','consumed')", name="ck_fx_locks_status"),
        CheckConstraint("expires_at > locked_at", name="ck_fx_locks_window"),
        CheckConstraint(
            "(status = 'active' AND consumed_at IS NULL AND consumed_approval_id IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL "
            "AND consumed_approval_id IS NOT NULL)",
            name="ck_fx_locks_lifecycle",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= locked_at AND consumed_at < expires_at)",
            name="ck_fx_locks_consumption_window",
        ),
        CheckConstraint(
            "char_length(integrity_digest) = 64",
            name="ck_fx_locks_digest_length",
        ),
        Index("ix_fx_locks_mission_time", "mission_id", "locked_at", "id"),
        Index("ix_fx_locks_buyer_time", "buyer_id", "locked_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_source: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    integrity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_approval_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("procurement_approvals.id", ondelete="RESTRICT"),
        unique=True,
    )


class FxLockConversionRow(Base):
    __tablename__ = "fx_lock_conversions"
    __table_args__ = (
        UniqueConstraint(
            "lock_id",
            "global_rank",
            name="uq_fx_lock_conversions_global_rank",
        ),
        CheckConstraint(
            "quote_revision_number >= 1",
            name="ck_fx_lock_conversions_quote_revision",
        ),
        CheckConstraint("global_rank >= 1", name="ck_fx_lock_conversions_global_rank"),
        CheckConstraint(
            "global_score_total_basis_points >= 0",
            name="ck_fx_lock_conversions_global_score",
        ),
    )

    lock_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fx_locks.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    quote_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    original_expected_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_worst_case_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    converted_expected_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    converted_worst_case_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fx_rates.id", ondelete="RESTRICT"),
    )
    rate_text: Mapped[str] = mapped_column(String(80), nullable=False)
    fx_source: Mapped[str] = mapped_column(String(64), nullable=False)
    fx_source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    fx_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rate_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_minor_exponent: Mapped[int | None] = mapped_column(Integer)
    target_minor_exponent: Mapped[int | None] = mapped_column(Integer)
    conversion_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rounding_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    global_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    global_score_method: Mapped[str] = mapped_column(String(96), nullable=False)
    global_score_total_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
