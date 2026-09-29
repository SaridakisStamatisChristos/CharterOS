from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class DisruptionRow(Base):
    __tablename__ = "disruptions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_disruptions_version_positive"),
        CheckConstraint(
            "disruption_type IN "
            "('delay','aircraft_unavailable','crew_unavailable','airport_restriction',"
            "'technical','weather','other')",
            name="ck_disruptions_type",
        ),
        CheckConstraint(
            "status IN "
            "('open','proposed','awaiting_buyer','buyer_approved','buyer_rejected','resolved')",
            name="ck_disruptions_status",
        ),
        CheckConstraint(
            "(status = 'resolved' AND selected_proposal_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND resolution_outcome IS NOT NULL "
            "AND booking_state_at_resolution IS NOT NULL) OR "
            "(status <> 'resolved' AND selected_proposal_id IS NULL "
            "AND selected_commercial_change_id IS NULL "
            "AND selected_buyer_decision_id IS NULL AND resolved_at IS NULL "
            "AND resolution_outcome IS NULL AND booking_state_at_resolution IS NULL)",
            name="ck_disruptions_resolution_lifecycle",
        ),
        Index("ix_disruptions_booking_detected", "booking_id", "detected_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disruption_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    current_proposal_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_commercial_change_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    latest_buyer_decision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    selected_proposal_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    selected_commercial_change_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    selected_buyer_decision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_outcome: Mapped[str | None] = mapped_column(String(2000))
    booking_state_at_resolution: Mapped[str | None] = mapped_column(String(32))


class DisruptionProposalRow(Base):
    __tablename__ = "disruption_proposals"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="ck_disruption_proposals_revision_positive"),
        CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_disruption_proposals_status",
        ),
        CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_disruption_proposals_lifecycle",
        ),
        CheckConstraint(
            "(departure_start IS NULL AND departure_end IS NULL) OR "
            "(departure_start IS NOT NULL AND departure_end IS NOT NULL "
            "AND departure_start < departure_end)",
            name="ck_disruption_proposals_departure_window",
        ),
        Index(
            "uq_disruption_proposals_current",
            "disruption_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        Index(
            "uq_disruption_proposals_revision",
            "disruption_id",
            "revision_number",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    disruption_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruptions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_proposal_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruption_proposals.id", ondelete="RESTRICT"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    proposed_aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    departure_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    departure_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requires_buyer_decision: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence: Mapped[str | None] = mapped_column(String(2000))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisruptionCommercialChangeRow(Base):
    __tablename__ = "disruption_commercial_changes"
    __table_args__ = (
        CheckConstraint(
            "revision_number > 0",
            name="ck_disruption_commercial_changes_revision_positive",
        ),
        CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_disruption_commercial_changes_status",
        ),
        CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_disruption_commercial_changes_lifecycle",
        ),
        CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_disruption_commercial_currency",
        ),
        CheckConstraint(
            "resulting_expected_total_minor >= 0",
            name="ck_disruption_commercial_expected_nonnegative",
        ),
        CheckConstraint(
            "resulting_worst_case_total_minor >= 0",
            name="ck_disruption_commercial_worst_nonnegative",
        ),
        Index(
            "uq_disruption_commercial_current",
            "proposal_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        Index(
            "uq_disruption_commercial_revision",
            "proposal_id",
            "revision_number",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    disruption_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruptions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruption_proposals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_change_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruption_commercial_changes.id", ondelete="RESTRICT"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    original_quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    original_expected_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_worst_case_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    known_adjustment_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    conditional_adjustment_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resulting_expected_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resulting_worst_case_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    terms_summary: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisruptionBuyerDecisionRow(Base):
    __tablename__ = "disruption_buyer_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_disruption_buyer_decisions_value",
        ),
        Index(
            "ix_disruption_buyer_decisions_disruption_time",
            "disruption_id",
            "decided_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    disruption_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruptions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruption_proposals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    commercial_change_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("disruption_commercial_changes.id", ondelete="RESTRICT"),
    )
    evidence_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
