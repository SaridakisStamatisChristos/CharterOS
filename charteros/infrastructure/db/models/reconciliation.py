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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class FinancialReconciliationRow(Base):
    __tablename__ = "financial_reconciliations"
    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_financial_reconciliations_booking"),
        CheckConstraint("version > 0", name="ck_financial_reconciliations_version_positive"),
        CheckConstraint(
            "status IN ('open','invoice_submitted','disputed','variance_approved','completed')",
            name="ck_financial_reconciliations_status",
        ),
        CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_financial_reconciliations_currency",
        ),
        CheckConstraint(
            "quote_revision_number > 0",
            name="ck_financial_reconciliations_quote_revision_positive",
        ),
        CheckConstraint(
            "booked_amount_minor > 0",
            name="ck_financial_reconciliations_booked_positive",
        ),
        CheckConstraint(
            "booked_worst_case_amount_minor >= booked_amount_minor",
            name="ck_financial_reconciliations_booked_worst_ge_booked",
        ),
        CheckConstraint(
            "(status = 'open' AND current_invoice_revision_id IS NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'invoice_submitted' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'disputed' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NOT NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'variance_approved' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NOT NULL) OR "
            "(status = 'completed' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL)",
            name="ck_financial_reconciliations_active_lifecycle",
        ),
        CheckConstraint(
            "(status = 'completed' AND final_invoice_revision_id IS NOT NULL "
            "AND approved_variance_minor IS NOT NULL AND final_payable_minor IS NOT NULL "
            "AND final_payable_minor > 0 AND completed_at IS NOT NULL "
            "AND final_invoice_revision_id = current_invoice_revision_id) OR "
            "(status <> 'completed' AND final_invoice_revision_id IS NULL "
            "AND approved_variance_minor IS NULL AND final_payable_minor IS NULL "
            "AND completed_at IS NULL)",
            name="ck_financial_reconciliations_completion_evidence",
        ),
        Index("ix_financial_reconciliations_operator_status", "operator_id", "status"),
        Index("ix_financial_reconciliations_buyer_status", "buyer_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    accepted_quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
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
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    booked_worst_case_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_invoice_revision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_dispute_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_variance_approval_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    final_invoice_revision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_variance_minor: Mapped[int | None] = mapped_column(BigInteger)
    final_payable_minor: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperatorInvoiceRevisionRow(Base):
    __tablename__ = "operator_invoice_revisions"
    __table_args__ = (
        UniqueConstraint(
            "reconciliation_id",
            "revision_number",
            name="uq_operator_invoice_revisions_number",
        ),
        UniqueConstraint(
            "supersedes_invoice_revision_id",
            name="uq_operator_invoice_revisions_supersedes",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_operator_invoice_revisions_number_positive",
        ),
        CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_operator_invoice_revisions_status",
        ),
        CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_operator_invoice_revisions_currency",
        ),
        CheckConstraint(
            "booked_amount_minor > 0 AND total_amount_minor > 0",
            name="ck_operator_invoice_revisions_amounts_positive",
        ),
        CheckConstraint(
            "variance_minor = total_amount_minor - booked_amount_minor",
            name="ck_operator_invoice_revisions_variance_exact",
        ),
        CheckConstraint(
            "(variance_minor <= 0) OR surcharge_reason IS NOT NULL",
            name="ck_operator_invoice_revisions_positive_variance_reason",
        ),
        CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_operator_invoice_revisions_lifecycle",
        ),
        Index(
            "uq_operator_invoice_revisions_current",
            "reconciliation_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reconciliation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("financial_reconciliations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_invoice_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operator_invoice_revisions.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    invoice_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    booked_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    variance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    surcharge_reason: Mapped[str | None] = mapped_column(String(2000))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperatorInvoiceLineRow(Base):
    __tablename__ = "operator_invoice_lines"
    __table_args__ = (
        CheckConstraint("line_number > 0", name="ck_operator_invoice_lines_number_positive"),
        CheckConstraint("amount_minor <> 0", name="ck_operator_invoice_lines_nonzero"),
        CheckConstraint(
            "category IN "
            "('charter_base','repositioning','fuel_surcharge','airport_fees','handling',"
            "'parking','crew_overnight','catering','deicing','permits','taxes',"
            "'broker_service_fee','other','credit')",
            name="ck_operator_invoice_lines_category",
        ),
        CheckConstraint(
            "(category = 'credit' AND amount_minor < 0) OR "
            "(category <> 'credit' AND amount_minor > 0)",
            name="ck_operator_invoice_lines_sign",
        ),
    )

    invoice_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operator_invoice_revisions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000))


class ReconciliationDisputeRow(Base):
    __tablename__ = "reconciliation_disputes"
    __table_args__ = (
        UniqueConstraint(
            "invoice_revision_id",
            name="uq_reconciliation_disputes_invoice_revision",
        ),
        CheckConstraint(
            "disputed_amount_minor > 0",
            name="ck_reconciliation_disputes_amount_positive",
        ),
        Index("ix_reconciliation_disputes_reconciliation", "reconciliation_id", "opened_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reconciliation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("financial_reconciliations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invoice_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operator_invoice_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disputed_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VarianceApprovalRow(Base):
    __tablename__ = "reconciliation_variance_approvals"
    __table_args__ = (
        UniqueConstraint(
            "invoice_revision_id",
            name="uq_reconciliation_variance_approvals_invoice_revision",
        ),
        UniqueConstraint(
            "resolves_dispute_id",
            name="uq_reconciliation_variance_approvals_resolves_dispute",
        ),
        CheckConstraint(
            "approved_variance_minor >= 0",
            name="ck_reconciliation_variance_approvals_nonnegative",
        ),
        Index(
            "ix_reconciliation_variance_approvals_reconciliation",
            "reconciliation_id",
            "approved_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reconciliation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("financial_reconciliations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invoice_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operator_invoice_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_variance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolves_dispute_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reconciliation_disputes.id", ondelete="RESTRICT"),
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
