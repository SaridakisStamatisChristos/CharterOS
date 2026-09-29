from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class ProcurementApprovalRow(Base):
    __tablename__ = "procurement_approvals"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_procurement_approvals_version_positive"),
        CheckConstraint(
            "status IN ('approved','superseded','consumed')",
            name="ck_procurement_approvals_status",
        ),
        CheckConstraint(
            "(status = 'approved' AND superseded_at IS NULL AND consumed_at IS NULL "
            "AND booking_id IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL AND consumed_at IS NULL "
            "AND booking_id IS NULL) OR "
            "(status = 'consumed' AND superseded_at IS NULL AND consumed_at IS NOT NULL "
            "AND booking_id IS NOT NULL)",
            name="ck_procurement_approvals_lifecycle",
        ),
        CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= approved_at",
            name="ck_procurement_approvals_superseded_after_approval",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= approved_at",
            name="ck_procurement_approvals_consumed_after_approval",
        ),
        Index(
            "uq_procurement_approvals_active_mission",
            "mission_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
        Index(
            "ix_procurement_approvals_mission_time",
            "mission_id",
            "approved_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    supersedes_approval_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("procurement_approvals.id", ondelete="RESTRICT"),
        unique=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booking_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        unique=True,
    )
