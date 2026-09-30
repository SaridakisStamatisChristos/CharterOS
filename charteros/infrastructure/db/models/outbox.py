from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class OutboxConsumerReceiptRow(Base):
    __tablename__ = "outbox_consumer_receipts"
    __table_args__ = (
        CheckConstraint(
            "char_length(consumer_name) > 0",
            name="ck_outbox_consumer_receipts_name",
        ),
        CheckConstraint(
            "consumer_version > 0",
            name="ck_outbox_consumer_receipts_version",
        ),
        Index("ix_outbox_consumer_receipts_event_id", "event_id"),
    )

    consumer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("outbox_events.event_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    consumer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
