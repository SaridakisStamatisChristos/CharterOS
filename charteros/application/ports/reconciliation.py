from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.bookings import BookingId
from charteros.domain.reconciliation import (
    FinancialReconciliation,
    FinancialReconciliationId,
    OperatorInvoiceRevision,
    OperatorInvoiceRevisionId,
    ReconciliationDispute,
    ReconciliationDisputeId,
    VarianceApproval,
    VarianceApprovalId,
)


class FinancialReconciliationRepository(Protocol):
    def add(self, reconciliation: FinancialReconciliation) -> None: ...

    def get(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> FinancialReconciliation | None: ...

    def get_for_update(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> FinancialReconciliation | None: ...

    def get_for_booking(
        self,
        booking_id: BookingId,
    ) -> FinancialReconciliation | None: ...

    def save(
        self,
        reconciliation: FinancialReconciliation,
        *,
        expected_version: int,
    ) -> None: ...

    def add_invoice(self, invoice: OperatorInvoiceRevision) -> None: ...

    def get_invoice(
        self,
        invoice_revision_id: OperatorInvoiceRevisionId,
    ) -> OperatorInvoiceRevision | None: ...

    def get_current_invoice_for_update(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> OperatorInvoiceRevision | None: ...

    def list_invoices(
        self,
        reconciliation_id: FinancialReconciliationId,
        *,
        limit: int,
    ) -> tuple[OperatorInvoiceRevision, ...]: ...

    def supersede_invoice(
        self,
        invoice_revision_id: OperatorInvoiceRevisionId,
        *,
        superseded_at: datetime,
    ) -> None: ...

    def add_dispute(self, dispute: ReconciliationDispute) -> None: ...

    def get_dispute(
        self,
        dispute_id: ReconciliationDisputeId,
    ) -> ReconciliationDispute | None: ...

    def add_variance_approval(self, approval: VarianceApproval) -> None: ...

    def get_variance_approval(
        self,
        approval_id: VarianceApprovalId,
    ) -> VarianceApproval | None: ...
