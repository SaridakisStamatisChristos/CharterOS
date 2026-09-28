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


class RfqRow(Base):
    __tablename__ = "rfqs"
    __table_args__ = (
        UniqueConstraint("mission_id", "operator_id", name="uq_rfqs_mission_operator"),
        CheckConstraint(
            "status IN ('created','sent','acknowledged','quoted','declined','expired','withdrawn')",
            name="ck_rfqs_status",
        ),
        CheckConstraint(
            "response_deadline IS NULL OR sent_at IS NOT NULL",
            name="ck_rfqs_deadline_requires_sent",
        ),
        CheckConstraint(
            "sent_at IS NULL OR sent_at >= created_at",
            name="ck_rfqs_sent_after_created",
        ),
        CheckConstraint(
            "response_deadline IS NULL OR response_deadline > sent_at",
            name="ck_rfqs_deadline_after_sent",
        ),
        CheckConstraint(
            "acknowledged_at IS NULL OR "
            "(acknowledged_at >= sent_at AND acknowledged_at < response_deadline)",
            name="ck_rfqs_acknowledged_in_window",
        ),
        CheckConstraint(
            "declined_at IS NULL OR (declined_at >= sent_at AND declined_at < response_deadline)",
            name="ck_rfqs_declined_in_window",
        ),
        CheckConstraint(
            "expired_at IS NULL OR expired_at >= response_deadline",
            name="ck_rfqs_expired_after_deadline",
        ),
        CheckConstraint(
            "(status = 'created' AND sent_at IS NULL AND response_deadline IS NULL "
            "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'sent' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'acknowledged' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'declined' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND declined_at IS NOT NULL AND expired_at IS NULL) OR "
            "(status = 'expired' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND expired_at IS NOT NULL AND declined_at IS NULL) OR "
            "(status IN ('quoted','withdrawn') AND sent_at IS NOT NULL "
            "AND response_deadline IS NOT NULL)",
            name="ck_rfqs_status_timestamps",
        ),
        Index("ix_rfqs_mission_status_deadline", "mission_id", "status", "response_deadline"),
        Index("ix_rfqs_operator_status_deadline", "operator_id", "status", "response_deadline"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None] = mapped_column(String(500))
