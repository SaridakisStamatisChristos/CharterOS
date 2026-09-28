from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from charteros.infrastructure.db.base import Base


class QuoteRow(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("rfq_id", "revision_number", name="uq_quotes_rfq_revision"),
        UniqueConstraint("supersedes_quote_id", name="uq_quotes_supersedes_quote"),
        CheckConstraint(
            "status IN ('submitted','accepted','rejected','expired','withdrawn','superseded')",
            name="ck_quotes_status",
        ),
        CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_quotes_currency",
        ),
        CheckConstraint("base_amount_minor > 0", name="ck_quotes_base_positive"),
        CheckConstraint(
            "repositioning_amount_minor IS NULL OR repositioning_amount_minor >= 0",
            name="ck_quotes_repositioning_nonnegative",
        ),
        CheckConstraint("revision_number > 0", name="ck_quotes_revision_positive"),
        CheckConstraint("valid_until > submitted_at", name="ck_quotes_validity_window"),
        CheckConstraint(
            "(revision_number = 1 AND supersedes_quote_id IS NULL) OR "
            "(revision_number > 1 AND supersedes_quote_id IS NOT NULL)",
            name="ck_quotes_revision_lineage",
        ),
        CheckConstraint(
            "(status = 'submitted' AND is_current = true "
            "AND accepted_at IS NULL AND rejected_at IS NULL "
            "AND expired_at IS NULL AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'accepted' AND is_current = false AND accepted_at IS NOT NULL "
            "AND rejected_at IS NULL AND expired_at IS NULL "
            "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'rejected' AND is_current = false AND rejected_at IS NOT NULL "
            "AND accepted_at IS NULL AND expired_at IS NULL "
            "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'expired' AND is_current = false AND expired_at IS NOT NULL "
            "AND accepted_at IS NULL AND rejected_at IS NULL "
            "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'withdrawn' AND is_current = false AND withdrawn_at IS NOT NULL "
            "AND accepted_at IS NULL AND rejected_at IS NULL "
            "AND expired_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND is_current = false AND superseded_at IS NOT NULL "
            "AND accepted_at IS NULL AND rejected_at IS NULL "
            "AND expired_at IS NULL AND withdrawn_at IS NULL)",
            name="ck_quotes_status_timestamps",
        ),
        Index("ix_quotes_rfq_revision", "rfq_id", "revision_number"),
        Index("ix_quotes_aircraft_status", "aircraft_id", "status"),
        Index(
            "uq_quotes_current_rfq",
            "rfq_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rfq_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rfqs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repositioning_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    inclusions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exclusions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    cancellation_terms: Mapped[str | None] = mapped_column(Text)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_quote_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    components: Mapped[list[QuotePriceComponentRow]] = relationship(
        back_populates="quote",
        lazy="selectin",
        order_by="QuotePriceComponentRow.line_number",
    )


class QuotePriceComponentRow(Base):
    __tablename__ = "quote_price_components"
    __table_args__ = (
        CheckConstraint("line_number >= 0", name="ck_quote_components_line_number"),
        CheckConstraint("amount_minor >= 0", name="ck_quote_components_amount_nonnegative"),
        CheckConstraint(
            "category IN ('fuel_surcharge','airport_fees','handling','parking',"
            "'crew_overnight','catering','deicing','permits','taxes',"
            "'broker_service_fee','other')",
            name="ck_quote_components_category",
        ),
        CheckConstraint(
            "applicability IN ('known','conditional')",
            name="ck_quote_components_applicability",
        ),
        CheckConstraint(
            "applicability = 'known' OR condition IS NOT NULL",
            name="ck_quote_components_conditional_condition",
        ),
    )

    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applicability: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="known",
    )
    condition: Mapped[str | None] = mapped_column(String(500))

    quote: Mapped[QuoteRow] = relationship(back_populates="components")
