from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.bookings import BookingId
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.reconciliation import (
    FinancialReconciliation,
    FinancialReconciliationStatus,
    InvoiceLine,
    InvoiceLineCategory,
    InvoiceRevisionStatus,
    OperatorInvoiceRevision,
    OperatorInvoiceRevisionId,
    ReconciliationDispute,
    ReconciliationDisputeId,
    VarianceApproval,
    VarianceApprovalId,
)
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
EUR = Currency("EUR")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _reconciliation() -> FinancialReconciliation:
    return FinancialReconciliation.open(
        booking_id=BookingId(_id(1)),
        accepted_quote_id=QuoteId(_id(2)),
        buyer_id=OrganizationId(_id(3)),
        operator_id=OperatorId(_id(4)),
        currency=EUR,
        quote_normalization_version="v1",
        quote_revision_number=1,
        booked_amount=Money(8_000_000, EUR),
        booked_worst_case_amount=Money(8_100_000, EUR),
        commercial_change_ids=(),
        opened_at=NOW,
        actor_id=OperatorId(_id(4)),
    )


def _invoice(
    reconciliation: FinancialReconciliation,
    *,
    total: int,
    revision: int = 1,
    supersedes: OperatorInvoiceRevisionId | None = None,
    surcharge_reason: str | None = None,
    submitted_at: datetime | None = None,
) -> OperatorInvoiceRevision:
    lines: tuple[InvoiceLine, ...] = (
        InvoiceLine(
            line_number=1,
            category=InvoiceLineCategory.CHARTER_BASE,
            label="Booked charter",
            amount=Money(8_000_000, EUR),
        ),
    )
    if total != 8_000_000:
        delta = total - 8_000_000
        lines = (
            *lines,
            InvoiceLine(
                line_number=2,
                category=(
                    InvoiceLineCategory.FUEL_SURCHARGE if delta > 0 else InvoiceLineCategory.CREDIT
                ),
                label="Settlement adjustment",
                amount=Money(delta, EUR),
                reason="Post-operation adjustment",
            ),
        )
    return OperatorInvoiceRevision(
        id=OperatorInvoiceRevisionId.new(),
        reconciliation_id=reconciliation.id,
        revision_number=revision,
        supersedes_invoice_revision_id=supersedes,
        status=InvoiceRevisionStatus.CURRENT,
        invoice_reference=f"INV-{revision}",
        currency=EUR,
        booked_amount=reconciliation.booked_amount,
        line_items=lines,
        total_amount=Money(total, EUR),
        variance=Money(total - 8_000_000, EUR),
        surcharge_reason=surcharge_reason,
        submitted_at=submitted_at or NOW + timedelta(minutes=1),
    )


def test_positive_invoice_variance_requires_reason_and_exact_line_total() -> None:
    reconciliation = _reconciliation()

    with pytest.raises(DomainValidationError, match="surcharge reason"):
        _invoice(reconciliation, total=8_300_000)

    with pytest.raises(DomainValidationError, match="sum exactly"):
        OperatorInvoiceRevision(
            id=OperatorInvoiceRevisionId.new(),
            reconciliation_id=reconciliation.id,
            revision_number=1,
            supersedes_invoice_revision_id=None,
            status=InvoiceRevisionStatus.CURRENT,
            invoice_reference="INV-BAD",
            currency=EUR,
            booked_amount=reconciliation.booked_amount,
            line_items=(
                InvoiceLine(
                    line_number=1,
                    category=InvoiceLineCategory.CHARTER_BASE,
                    label="Booked charter",
                    amount=Money(8_000_000, EUR),
                ),
            ),
            total_amount=Money(8_100_000, EUR),
            variance=Money(100_000, EUR),
            surcharge_reason="Fuel",
            submitted_at=NOW + timedelta(minutes=1),
        )


def test_dispute_must_be_explicitly_resolved_before_variance_approval_and_completion() -> None:
    reconciliation = _reconciliation()
    invoice = _invoice(
        reconciliation,
        total=8_300_000,
        surcharge_reason="Unexpected destination handling and fuel",
    )
    reconciliation.record_invoice(invoice, actor_id=reconciliation.operator_id)

    dispute = ReconciliationDispute(
        id=ReconciliationDisputeId.new(),
        reconciliation_id=reconciliation.id,
        invoice_revision_id=invoice.id,
        buyer_id=reconciliation.buyer_id,
        disputed_amount=Money(300_000, EUR),
        reason="Surcharge support is incomplete",
        opened_at=NOW + timedelta(minutes=2),
    )
    reconciliation.record_dispute(dispute, invoice=invoice)
    assert reconciliation.status is FinancialReconciliationStatus.DISPUTED

    wrong_approval = VarianceApproval(
        id=VarianceApprovalId.new(),
        reconciliation_id=reconciliation.id,
        invoice_revision_id=invoice.id,
        buyer_id=reconciliation.buyer_id,
        approved_variance=Money(150_000, EUR),
        resolves_dispute_id=None,
        approved_at=NOW + timedelta(minutes=3),
    )
    with pytest.raises(DomainValidationError, match="exact current dispute"):
        reconciliation.record_variance_approval(wrong_approval, invoice=invoice)

    approval = VarianceApproval(
        id=VarianceApprovalId.new(),
        reconciliation_id=reconciliation.id,
        invoice_revision_id=invoice.id,
        buyer_id=reconciliation.buyer_id,
        approved_variance=Money(150_000, EUR),
        resolves_dispute_id=dispute.id,
        approved_at=NOW + timedelta(minutes=3),
        note="Buyer accepts half the claimed variance",
    )
    reconciliation.record_variance_approval(approval, invoice=invoice)
    assert (
        reconciliation.status.value
        == FinancialReconciliationStatus.VARIANCE_APPROVED.value
    )
    assert reconciliation.current_dispute_id is None

    payable = reconciliation.complete(
        invoice=invoice,
        approval=approval,
        completed_at=NOW + timedelta(minutes=4),
        actor_id=reconciliation.operator_id,
    )
    assert payable == Money(8_150_000, EUR)
    assert reconciliation.final_payable == Money(8_150_000, EUR)
    assert reconciliation.approved_variance == Money(150_000, EUR)
    assert reconciliation.status is FinancialReconciliationStatus.COMPLETED

    with pytest.raises(DomainValidationError, match="terminal"):
        reconciliation.record_invoice(
            _invoice(
                reconciliation,
                total=8_000_000,
                revision=2,
                supersedes=invoice.id,
                submitted_at=NOW + timedelta(minutes=5),
            ),
            actor_id=reconciliation.operator_id,
        )


def test_nonpositive_variance_needs_no_buyer_approval_and_pays_invoice_total() -> None:
    reconciliation = _reconciliation()
    invoice = _invoice(reconciliation, total=7_900_000)
    reconciliation.record_invoice(invoice, actor_id=reconciliation.operator_id)

    payable = reconciliation.complete(
        invoice=invoice,
        approval=None,
        completed_at=NOW + timedelta(minutes=2),
        actor_id=reconciliation.operator_id,
    )

    assert payable == Money(7_900_000, EUR)
    assert reconciliation.approved_variance == Money.zero(EUR)
    assert reconciliation.final_invoice_revision_id == invoice.id
    assert [event.event_type for event in reconciliation.pending_events] == [
        "FINANCIAL_RECONCILIATION_OPENED",
        "OPERATOR_INVOICE_SUBMITTED",
        "FINANCIAL_RECONCILIATION_COMPLETED",
    ]


def test_invoice_revisions_preserve_immutable_supersession_lineage() -> None:
    reconciliation = _reconciliation()
    first = _invoice(
        reconciliation,
        total=8_250_000,
        surcharge_reason="Handling",
    )
    reconciliation.record_invoice(first, actor_id=reconciliation.operator_id)

    second = _invoice(
        reconciliation,
        total=8_100_000,
        revision=2,
        supersedes=first.id,
        surcharge_reason="Reduced handling",
        submitted_at=NOW + timedelta(minutes=3),
    )
    reconciliation.record_invoice_superseded(
        first.id,
        replacement_invoice_revision_id=second.id,
        superseded_at=NOW + timedelta(minutes=3),
        actor_id=reconciliation.operator_id,
    )
    reconciliation.record_invoice(second, actor_id=reconciliation.operator_id)

    assert reconciliation.current_invoice_revision_id == second.id
    assert reconciliation.status is FinancialReconciliationStatus.INVOICE_SUBMITTED
    assert [event.event_type for event in reconciliation.pending_events] == [
        "FINANCIAL_RECONCILIATION_OPENED",
        "OPERATOR_INVOICE_SUBMITTED",
        "OPERATOR_INVOICE_SUPERSEDED",
        "OPERATOR_INVOICE_SUBMITTED",
    ]
