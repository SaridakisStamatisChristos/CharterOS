from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
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


class ContractRow(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_contracts_booking"),
        CheckConstraint("version > 0", name="ck_contracts_version_positive"),
        CheckConstraint(
            "document_version > 0",
            name="ck_contracts_document_version_positive",
        ),
        CheckConstraint(
            "status IN ('pending_acceptance','partially_accepted','accepted')",
            name="ck_contracts_status",
        ),
        CheckConstraint(
            "(status = 'pending_acceptance' AND buyer_signed_at IS NULL "
            "AND operator_signed_at IS NULL AND accepted_at IS NULL) OR "
            "(status = 'partially_accepted' AND accepted_at IS NULL AND "
            "((buyer_signed_at IS NOT NULL AND operator_signed_at IS NULL) OR "
            "(buyer_signed_at IS NULL AND operator_signed_at IS NOT NULL))) OR "
            "(status = 'accepted' AND buyer_signed_at IS NOT NULL "
            "AND operator_signed_at IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_contracts_acceptance_state",
        ),
        CheckConstraint(
            "buyer_signed_at IS NULL OR buyer_signed_at >= created_at",
            name="ck_contracts_buyer_signed_after_create",
        ),
        CheckConstraint(
            "operator_signed_at IS NULL OR operator_signed_at >= created_at",
            name="ck_contracts_operator_signed_after_create",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR "
            "(accepted_at >= buyer_signed_at AND accepted_at >= operator_signed_at)",
            name="ck_contracts_accepted_after_signatures",
        ),
        Index("ix_contracts_status", "status"),
        Index("ix_contracts_buyer_status", "buyer_id", "status"),
        Index("ix_contracts_operator_status", "operator_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_reference: Mapped[str] = mapped_column(String(1000), nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, str]] = mapped_column("metadata", JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    buyer_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
