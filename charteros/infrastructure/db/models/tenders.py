from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
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


class TenderRow(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_tenders_mission"),
        CheckConstraint("version > 0", name="ck_tenders_version_positive"),
        CheckConstraint(
            "status IN ('draft','open','best_and_final','closed','awarded')",
            name="ck_tenders_status",
        ),
        CheckConstraint("opens_at < deadline_at", name="ck_tenders_window"),
        CheckConstraint("created_at < deadline_at", name="ck_tenders_created_before_deadline"),
        CheckConstraint(
            "(status = 'draft' AND opened_at IS NULL "
            "AND best_and_final_requested_at IS NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'open' AND opened_at IS NOT NULL "
            "AND best_and_final_requested_at IS NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'best_and_final' AND opened_at IS NOT NULL "
            "AND best_and_final_requested_at IS NOT NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'closed' AND opened_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'awarded' AND opened_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND awarded_quote_id IS NOT NULL AND booking_id IS NOT NULL "
            "AND awarded_at IS NOT NULL)",
            name="ck_tenders_lifecycle_shape",
        ),
        CheckConstraint(
            "opened_at IS NULL OR (opened_at >= opens_at AND opened_at < deadline_at)",
            name="ck_tenders_opened_inside_window",
        ),
        CheckConstraint(
            "best_and_final_requested_at IS NULL OR "
            "(best_and_final_requested_at >= opened_at "
            "AND best_and_final_requested_at < deadline_at)",
            name="ck_tenders_bafo_inside_window",
        ),
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= deadline_at",
            name="ck_tenders_closed_after_deadline",
        ),
        CheckConstraint(
            "awarded_at IS NULL OR awarded_at >= closed_at",
            name="ck_tenders_award_after_close",
        ),
        Index("ix_tenders_status_deadline", "status", "deadline_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sealed_bid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    best_and_final_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    awarded_quote_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
    )
    booking_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
    )
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenderInvitationRow(Base):
    __tablename__ = "tender_invitations"
    __table_args__ = (
        UniqueConstraint("tender_id", "operator_id", name="uq_tender_invitation_operator"),
        UniqueConstraint("rfq_id", name="uq_tender_invitation_rfq"),
        CheckConstraint(
            "status IN ('invited','accepted','declined')",
            name="ck_tender_invitations_status",
        ),
        CheckConstraint(
            "(status = 'invited' AND responded_at IS NULL) OR "
            "(status IN ('accepted','declined') AND responded_at IS NOT NULL)",
            name="ck_tender_invitations_response_shape",
        ),
        CheckConstraint(
            "responded_at IS NULL OR responded_at >= invited_at",
            name="ck_tender_invitations_response_time",
        ),
        CheckConstraint(
            "best_and_final_quote_id IS NULL OR best_and_final_quote_id = last_quote_id",
            name="ck_tender_invitations_bafo_is_latest",
        ),
        Index("ix_tender_invitations_tender_status", "tender_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tender_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rfq_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rfqs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_quote_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
    )
    best_and_final_quote_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
    )


class TenderAdminCorrectionRow(Base):
    __tablename__ = "tender_admin_corrections"
    __table_args__ = (
        CheckConstraint("char_length(target_type) > 0", name="ck_tender_correction_target_type"),
        CheckConstraint("char_length(field_name) > 0", name="ck_tender_correction_field_name"),
        CheckConstraint("char_length(reason) > 0", name="ck_tender_correction_reason"),
        Index("ix_tender_admin_corrections_tender_time", "tender_id", "corrected_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tender_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_value: Mapped[object] = mapped_column(JSON, nullable=False)
    replacement_value: Mapped[object] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    causation_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("outbox_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
