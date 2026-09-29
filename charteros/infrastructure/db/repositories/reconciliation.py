from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.bookings import BookingId
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.reconciliation import (
    FinancialReconciliation,
    FinancialReconciliationId,
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
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.infrastructure.db.models.reconciliation import (
    FinancialReconciliationRow,
    OperatorInvoiceLineRow,
    OperatorInvoiceRevisionRow,
    ReconciliationDisputeRow,
    VarianceApprovalRow,
)


def _reconciliation_from_row(row: FinancialReconciliationRow) -> FinancialReconciliation:
    currency = Currency(row.currency)
    return FinancialReconciliation(
        FinancialReconciliationId(row.id),
        booking_id=BookingId(row.booking_id),
        accepted_quote_id=QuoteId(row.accepted_quote_id),
        buyer_id=OrganizationId(row.buyer_id),
        operator_id=OperatorId(row.operator_id),
        currency=currency,
        quote_normalization_version=row.quote_normalization_version,
        quote_revision_number=row.quote_revision_number,
        booked_amount=Money(row.booked_amount_minor, currency),
        booked_worst_case_amount=Money(row.booked_worst_case_amount_minor, currency),
        opened_at=row.opened_at,
        status=FinancialReconciliationStatus(row.status),
        current_invoice_revision_id=(
            OperatorInvoiceRevisionId(row.current_invoice_revision_id)
            if row.current_invoice_revision_id is not None
            else None
        ),
        current_dispute_id=(
            ReconciliationDisputeId(row.current_dispute_id)
            if row.current_dispute_id is not None
            else None
        ),
        current_variance_approval_id=(
            VarianceApprovalId(row.current_variance_approval_id)
            if row.current_variance_approval_id is not None
            else None
        ),
        final_invoice_revision_id=(
            OperatorInvoiceRevisionId(row.final_invoice_revision_id)
            if row.final_invoice_revision_id is not None
            else None
        ),
        approved_variance=(
            Money(row.approved_variance_minor, currency)
            if row.approved_variance_minor is not None
            else None
        ),
        final_payable=(
            Money(row.final_payable_minor, currency)
            if row.final_payable_minor is not None
            else None
        ),
        completed_at=row.completed_at,
        version=row.version,
    )


def _invoice_from_row(
    row: OperatorInvoiceRevisionRow,
    lines: tuple[OperatorInvoiceLineRow, ...],
) -> OperatorInvoiceRevision:
    currency = Currency(row.currency)
    return OperatorInvoiceRevision(
        id=OperatorInvoiceRevisionId(row.id),
        reconciliation_id=FinancialReconciliationId(row.reconciliation_id),
        revision_number=row.revision_number,
        supersedes_invoice_revision_id=(
            OperatorInvoiceRevisionId(row.supersedes_invoice_revision_id)
            if row.supersedes_invoice_revision_id is not None
            else None
        ),
        status=InvoiceRevisionStatus(row.status),
        invoice_reference=row.invoice_reference,
        currency=currency,
        booked_amount=Money(row.booked_amount_minor, currency),
        line_items=tuple(
            InvoiceLine(
                line_number=line.line_number,
                category=InvoiceLineCategory(line.category),
                label=line.label,
                amount=Money(line.amount_minor, currency),
                reason=line.reason,
            )
            for line in lines
        ),
        total_amount=Money(row.total_amount_minor, currency),
        variance=Money(row.variance_minor, currency),
        surcharge_reason=row.surcharge_reason,
        submitted_at=row.submitted_at,
        superseded_at=row.superseded_at,
    )


def _dispute_from_row(
    row: ReconciliationDisputeRow,
    *,
    currency: Currency,
) -> ReconciliationDispute:
    return ReconciliationDispute(
        id=ReconciliationDisputeId(row.id),
        reconciliation_id=FinancialReconciliationId(row.reconciliation_id),
        invoice_revision_id=OperatorInvoiceRevisionId(row.invoice_revision_id),
        buyer_id=OrganizationId(row.buyer_id),
        disputed_amount=Money(row.disputed_amount_minor, currency),
        reason=row.reason,
        opened_at=row.opened_at,
    )


def _approval_from_row(row: VarianceApprovalRow, *, currency: Currency) -> VarianceApproval:
    return VarianceApproval(
        id=VarianceApprovalId(row.id),
        reconciliation_id=FinancialReconciliationId(row.reconciliation_id),
        invoice_revision_id=OperatorInvoiceRevisionId(row.invoice_revision_id),
        buyer_id=OrganizationId(row.buyer_id),
        approved_variance=Money(row.approved_variance_minor, currency),
        resolves_dispute_id=(
            ReconciliationDisputeId(row.resolves_dispute_id)
            if row.resolves_dispute_id is not None
            else None
        ),
        approved_at=row.approved_at,
        note=row.note,
    )


class SqlAlchemyFinancialReconciliationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reconciliation: FinancialReconciliation) -> None:
        self._session.add(
            FinancialReconciliationRow(
                id=reconciliation.id.value,
                version=reconciliation.version,
                booking_id=reconciliation.booking_id.value,
                accepted_quote_id=reconciliation.accepted_quote_id.value,
                buyer_id=reconciliation.buyer_id.value,
                operator_id=reconciliation.operator_id.value,
                currency=str(reconciliation.currency),
                quote_normalization_version=reconciliation.quote_normalization_version,
                quote_revision_number=reconciliation.quote_revision_number,
                booked_amount_minor=reconciliation.booked_amount.amount_minor,
                booked_worst_case_amount_minor=(
                    reconciliation.booked_worst_case_amount.amount_minor
                ),
                opened_at=reconciliation.opened_at,
                status=reconciliation.status.value,
                current_invoice_revision_id=None,
                current_dispute_id=None,
                current_variance_approval_id=None,
                final_invoice_revision_id=None,
                approved_variance_minor=None,
                final_payable_minor=None,
                completed_at=None,
            )
        )
        self._flush("booking already has a financial reconciliation")

    def get(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> FinancialReconciliation | None:
        row = self._session.get(FinancialReconciliationRow, reconciliation_id.value)
        return _reconciliation_from_row(row) if row is not None else None

    def get_for_update(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> FinancialReconciliation | None:
        row = self._session.scalar(
            select(FinancialReconciliationRow)
            .where(FinancialReconciliationRow.id == reconciliation_id.value)
            .with_for_update()
        )
        return _reconciliation_from_row(row) if row is not None else None

    def get_for_booking(
        self,
        booking_id: BookingId,
    ) -> FinancialReconciliation | None:
        row = self._session.scalar(
            select(FinancialReconciliationRow).where(
                FinancialReconciliationRow.booking_id == booking_id.value
            )
        )
        return _reconciliation_from_row(row) if row is not None else None

    def save(
        self,
        reconciliation: FinancialReconciliation,
        *,
        expected_version: int,
    ) -> None:
        updated = self._session.scalar(
            update(FinancialReconciliationRow)
            .where(
                FinancialReconciliationRow.id == reconciliation.id.value,
                FinancialReconciliationRow.version == expected_version,
            )
            .values(
                version=reconciliation.version,
                status=reconciliation.status.value,
                current_invoice_revision_id=(
                    reconciliation.current_invoice_revision_id.value
                    if reconciliation.current_invoice_revision_id is not None
                    else None
                ),
                current_dispute_id=(
                    reconciliation.current_dispute_id.value
                    if reconciliation.current_dispute_id is not None
                    else None
                ),
                current_variance_approval_id=(
                    reconciliation.current_variance_approval_id.value
                    if reconciliation.current_variance_approval_id is not None
                    else None
                ),
                final_invoice_revision_id=(
                    reconciliation.final_invoice_revision_id.value
                    if reconciliation.final_invoice_revision_id is not None
                    else None
                ),
                approved_variance_minor=(
                    reconciliation.approved_variance.amount_minor
                    if reconciliation.approved_variance is not None
                    else None
                ),
                final_payable_minor=(
                    reconciliation.final_payable.amount_minor
                    if reconciliation.final_payable is not None
                    else None
                ),
                completed_at=reconciliation.completed_at,
            )
            .returning(FinancialReconciliationRow.id)
        )
        if updated is None:
            raise OptimisticConcurrencyError(
                "financial reconciliation aggregate changed concurrently"
            )
        self._session.flush()

    def add_invoice(self, invoice: OperatorInvoiceRevision) -> None:
        invoice_row = OperatorInvoiceRevisionRow(
            id=invoice.id.value,
            reconciliation_id=invoice.reconciliation_id.value,
            revision_number=invoice.revision_number,
            supersedes_invoice_revision_id=(
                invoice.supersedes_invoice_revision_id.value
                if invoice.supersedes_invoice_revision_id is not None
                else None
            ),
            status=invoice.status.value,
            invoice_reference=invoice.invoice_reference,
            currency=str(invoice.currency),
            booked_amount_minor=invoice.booked_amount.amount_minor,
            total_amount_minor=invoice.total_amount.amount_minor,
            variance_minor=invoice.variance.amount_minor,
            surcharge_reason=invoice.surcharge_reason,
            submitted_at=invoice.submitted_at,
            superseded_at=invoice.superseded_at,
        )
        self._session.add(invoice_row)
        self._flush("invoice revision conflicts with persisted reconciliation evidence")

        self._session.add_all(
            [
                OperatorInvoiceLineRow(
                    invoice_revision_id=invoice.id.value,
                    line_number=line.line_number,
                    category=line.category.value,
                    label=line.label,
                    amount_minor=line.amount.amount_minor,
                    reason=line.reason,
                )
                for line in invoice.line_items
            ]
        )
        self._flush("invoice lines conflict with persisted reconciliation evidence")

    def get_invoice(
        self,
        invoice_revision_id: OperatorInvoiceRevisionId,
    ) -> OperatorInvoiceRevision | None:
        row = self._session.get(OperatorInvoiceRevisionRow, invoice_revision_id.value)
        if row is None:
            return None
        return self._invoice(row)

    def get_current_invoice_for_update(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> OperatorInvoiceRevision | None:
        row = self._session.scalar(
            select(OperatorInvoiceRevisionRow)
            .where(
                OperatorInvoiceRevisionRow.reconciliation_id == reconciliation_id.value,
                OperatorInvoiceRevisionRow.status == InvoiceRevisionStatus.CURRENT.value,
            )
            .with_for_update()
        )
        return self._invoice(row) if row is not None else None

    def list_invoices(
        self,
        reconciliation_id: FinancialReconciliationId,
        *,
        limit: int,
    ) -> tuple[OperatorInvoiceRevision, ...]:
        rows = self._session.scalars(
            select(OperatorInvoiceRevisionRow)
            .where(OperatorInvoiceRevisionRow.reconciliation_id == reconciliation_id.value)
            .order_by(
                OperatorInvoiceRevisionRow.revision_number,
                OperatorInvoiceRevisionRow.id,
            )
            .limit(limit)
        ).all()
        return tuple(self._invoice(row) for row in rows)

    def supersede_invoice(
        self,
        invoice_revision_id: OperatorInvoiceRevisionId,
        *,
        superseded_at: datetime,
    ) -> None:
        updated = self._session.scalar(
            update(OperatorInvoiceRevisionRow)
            .where(
                OperatorInvoiceRevisionRow.id == invoice_revision_id.value,
                OperatorInvoiceRevisionRow.status == InvoiceRevisionStatus.CURRENT.value,
            )
            .values(
                status=InvoiceRevisionStatus.SUPERSEDED.value,
                superseded_at=superseded_at,
            )
            .returning(OperatorInvoiceRevisionRow.id)
        )
        if updated is None:
            raise EntityConflictError("current invoice revision changed concurrently")
        self._session.flush()

    def add_dispute(self, dispute: ReconciliationDispute) -> None:
        self._session.add(
            ReconciliationDisputeRow(
                id=dispute.id.value,
                reconciliation_id=dispute.reconciliation_id.value,
                invoice_revision_id=dispute.invoice_revision_id.value,
                buyer_id=dispute.buyer_id.value,
                disputed_amount_minor=dispute.disputed_amount.amount_minor,
                reason=dispute.reason,
                opened_at=dispute.opened_at,
            )
        )
        self._flush("current invoice revision already has dispute evidence")

    def get_dispute(
        self,
        dispute_id: ReconciliationDisputeId,
    ) -> ReconciliationDispute | None:
        row = self._session.get(ReconciliationDisputeRow, dispute_id.value)
        if row is None:
            return None
        reconciliation = self.get(FinancialReconciliationId(row.reconciliation_id))
        if reconciliation is None:
            raise EntityConflictError("dispute references missing reconciliation")
        return _dispute_from_row(row, currency=reconciliation.currency)

    def add_variance_approval(self, approval: VarianceApproval) -> None:
        self._session.add(
            VarianceApprovalRow(
                id=approval.id.value,
                reconciliation_id=approval.reconciliation_id.value,
                invoice_revision_id=approval.invoice_revision_id.value,
                buyer_id=approval.buyer_id.value,
                approved_variance_minor=approval.approved_variance.amount_minor,
                resolves_dispute_id=(
                    approval.resolves_dispute_id.value
                    if approval.resolves_dispute_id is not None
                    else None
                ),
                approved_at=approval.approved_at,
                note=approval.note,
            )
        )
        self._flush("current invoice revision already has variance approval evidence")

    def get_variance_approval(
        self,
        approval_id: VarianceApprovalId,
    ) -> VarianceApproval | None:
        row = self._session.get(VarianceApprovalRow, approval_id.value)
        if row is None:
            return None
        reconciliation = self.get(FinancialReconciliationId(row.reconciliation_id))
        if reconciliation is None:
            raise EntityConflictError("variance approval references missing reconciliation")
        return _approval_from_row(row, currency=reconciliation.currency)

    def _invoice(self, row: OperatorInvoiceRevisionRow) -> OperatorInvoiceRevision:
        lines = tuple(
            self._session.scalars(
                select(OperatorInvoiceLineRow)
                .where(OperatorInvoiceLineRow.invoice_revision_id == row.id)
                .order_by(OperatorInvoiceLineRow.line_number)
            ).all()
        )
        return _invoice_from_row(row, lines)

    def _flush(self, message: str) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(message) from exc
