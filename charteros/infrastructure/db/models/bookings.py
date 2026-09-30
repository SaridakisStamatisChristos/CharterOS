from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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


class BookingRow(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_bookings_mission"),
        UniqueConstraint("accepted_quote_id", name="uq_bookings_accepted_quote"),
        CheckConstraint("version > 0", name="ck_bookings_version_positive"),
        CheckConstraint(
            "state IN ('pending_contract','contracted','payment_pending','confirmed',"
            "'pre_operation','operating','completed','reconciled','cancelled','expired')",
            name="ck_bookings_state",
        ),
        CheckConstraint(
            "state_changed_at >= created_at",
            name="ck_bookings_state_changed_at",
        ),
        CheckConstraint(
            "(state IN ('cancelled','expired') AND termination_reason IS NOT NULL "
            "AND termination_source IS NOT NULL) OR "
            "(state NOT IN ('cancelled','expired') AND termination_reason IS NULL "
            "AND termination_source IS NULL)",
            name="ck_bookings_termination_evidence",
        ),
        Index("ix_bookings_operator_state", "operator_id", "state"),
        Index("ix_bookings_aircraft_state", "aircraft_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    accepted_quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(String(32))
    termination_source: Mapped[str | None] = mapped_column(String(16))
