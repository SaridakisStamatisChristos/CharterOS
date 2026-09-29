from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.bookings import BookingId
from charteros.domain.disruptions import DisruptionCommercialChangeId
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money


class FinancialReconciliationId(TypedId):
    __slots__ = ()


class OperatorInvoiceRevisionId(TypedId):
    __slots__ = ()


class ReconciliationDisputeId(TypedId):
    __slots__ = ()


class VarianceApprovalId(TypedId):
    __slots__ = ()


class FinancialReconciliationStatus(StrEnum):
    OPEN = "open"
    INVOICE_SUBMITTED = "invoice_submitted"
    DISPUTED = "disputed"
    VARIANCE_APPROVED = "variance_approved"
    COMPLETED = "completed"


class InvoiceRevisionStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class InvoiceLineCategory(StrEnum):
    CHARTER_BASE = "charter_base"
    REPOSITIONING = "repositioning"
    FUEL_SURCHARGE = "fuel_surcharge"
    AIRPORT_FEES = "airport_fees"
    HANDLING = "handling"
    PARKING = "parking"
    CREW_OVERNIGHT = "crew_overnight"
    CATERING = "catering"
    DEICING = "deicing"
    PERMITS = "permits"
    TAXES = "taxes"
    BROKER_SERVICE_FEE = "broker_service_fee"
    OTHER = "other"
    CREDIT = "credit"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise DomainValidationError(f"{field_name} is required")
        return None
    normalized = " ".join(value.split())
    if not normalized:
        if required:
            raise DomainValidationError(f"{field_name} is required")
        return None
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = _text(
        value,
        field_name=field_name,
        max_length=max_length,
        required=True,
    )
    assert normalized is not None
    return normalized


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    line_number: int
    category: InvoiceLineCategory
    label: str
    amount: Money
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.line_number, int) or isinstance(self.line_number, bool):
            raise DomainValidationError("invoice line_number must be an integer")
        if self.line_number < 1:
            raise DomainValidationError("invoice line_number must be positive")
        if self.amount.amount_minor == 0:
            raise DomainValidationError("invoice line amount cannot be zero")
        if (
            self.category is InvoiceLineCategory.CREDIT
            and self.amount.amount_minor >= 0
        ):
            raise DomainValidationError("credit invoice line amount must be negative")
        if (
            self.category is not InvoiceLineCategory.CREDIT
            and self.amount.amount_minor < 0
        ):
            raise DomainValidationError("non-credit invoice line amount cannot be negative")
        object.__setattr__(
            self,
            "label",
            _required_text(self.label, field_name="invoice line label", max_length=200),
        )
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, field_name="invoice line reason", max_length=1000),
        )


@dataclass(frozen=True, slots=True)
class OperatorInvoiceRevision:
    id: OperatorInvoiceRevisionId
    reconciliation_id: FinancialReconciliationId
    revision_number: int
    supersedes_invoice_revision_id: OperatorInvoiceRevisionId | None
    status: InvoiceRevisionStatus
    invoice_reference: str
    currency: Currency
    booked_amount: Money
    line_items: tuple[InvoiceLine, ...]
    total_amount: Money
    variance: Money
    surcharge_reason: str | None
    submitted_at: datetime
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision_number, int) or isinstance(self.revision_number, bool):
            raise DomainValidationError("invoice revision_number must be an integer")
        if self.revision_number < 1:
            raise DomainValidationError("invoice revision_number must be positive")
        if self.revision_number == 1 and self.supersedes_invoice_revision_id is not None:
            raise DomainValidationError("initial invoice revision cannot supersede another")
        if self.revision_number > 1 and self.supersedes_invoice_revision_id is None:
            raise DomainValidationError("revised invoice must identify its predecessor")
        if self.supersedes_invoice_revision_id == self.id:
            raise DomainValidationError("invoice revision cannot supersede itself")
        if not 1 <= len(self.line_items) <= 256:
            raise DomainValidationError("invoice must contain 1 to 256 line items")

        reference = _required_text(
            self.invoice_reference,
            field_name="invoice_reference",
            max_length=160,
        )
        if self.booked_amount.currency != self.currency:
            raise DomainValidationError("booked amount currency must match invoice currency")
        if self.total_amount.currency != self.currency:
            raise DomainValidationError("invoice total currency must match invoice currency")
        if self.variance.currency != self.currency:
            raise DomainValidationError("invoice variance currency must match invoice currency")
        if self.total_amount.amount_minor <= 0:
            raise DomainValidationError("invoice total must be positive")

        expected_line_number = 1
        line_total = Money.zero(self.currency)
        for line in self.line_items:
            if line.line_number != expected_line_number:
                raise DomainValidationError(
                    "invoice line numbers must be contiguous starting at one"
                )
            if line.amount.currency != self.currency:
                raise DomainValidationError("invoice line currency must match invoice currency")
            line_total = line_total + line.amount
            expected_line_number += 1
        if line_total != self.total_amount:
            raise DomainValidationError("invoice line items must sum exactly to invoice total")

        expected_variance = self.total_amount - self.booked_amount
        if self.variance != expected_variance:
            raise DomainValidationError("invoice variance is inconsistent with booked amount")

        surcharge_reason = _text(
            self.surcharge_reason,
            field_name="surcharge_reason",
            max_length=2000,
        )
        if self.variance.amount_minor > 0 and surcharge_reason is None:
            raise DomainValidationError("positive invoice variance requires a surcharge reason")

        submitted_at = _utc(self.submitted_at, field_name="submitted_at")
        superseded_at = (
            _utc(self.superseded_at, field_name="superseded_at")
            if self.superseded_at is not None
            else None
        )
        if superseded_at is not None and superseded_at < submitted_at:
            raise DomainValidationError("invoice superseded_at cannot precede submitted_at")

        status = InvoiceRevisionStatus(self.status)
        if status is InvoiceRevisionStatus.CURRENT and superseded_at is not None:
            raise DomainValidationError("current invoice revision cannot be superseded")
        if status is InvoiceRevisionStatus.SUPERSEDED and superseded_at is None:
            raise DomainValidationError("superseded invoice revision requires superseded_at")

        object.__setattr__(self, "invoice_reference", reference)
        object.__setattr__(self, "surcharge_reason", surcharge_reason)
        object.__setattr__(self, "submitted_at", submitted_at)
        object.__setattr__(self, "superseded_at", superseded_at)
        object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class ReconciliationDispute:
    id: ReconciliationDisputeId
    reconciliation_id: FinancialReconciliationId
    invoice_revision_id: OperatorInvoiceRevisionId
    buyer_id: OrganizationId
    disputed_amount: Money
    reason: str
    opened_at: datetime

    def __post_init__(self) -> None:
        if self.disputed_amount.amount_minor <= 0:
            raise DomainValidationError("disputed amount must be positive")
        object.__setattr__(
            self,
            "reason",
            _required_text(self.reason, field_name="dispute reason", max_length=2000),
        )
        object.__setattr__(self, "opened_at", _utc(self.opened_at, field_name="opened_at"))


@dataclass(frozen=True, slots=True)
class VarianceApproval:
    id: VarianceApprovalId
    reconciliation_id: FinancialReconciliationId
    invoice_revision_id: OperatorInvoiceRevisionId
    buyer_id: OrganizationId
    approved_variance: Money
    resolves_dispute_id: ReconciliationDisputeId | None
    approved_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        if self.approved_variance.amount_minor < 0:
            raise DomainValidationError("approved variance cannot be negative")
        object.__setattr__(
            self,
            "approved_at",
            _utc(self.approved_at, field_name="approved_at"),
        )
        object.__setattr__(
            self,
            "note",
            _text(self.note, field_name="variance approval note", max_length=1000),
        )


class FinancialReconciliation(AggregateRoot[FinancialReconciliationId]):
    aggregate_type = "financial_reconciliation"

    def __init__(
        self,
        reconciliation_id: FinancialReconciliationId,
        *,
        booking_id: BookingId,
        accepted_quote_id: QuoteId,
        buyer_id: OrganizationId,
        operator_id: OperatorId,
        currency: Currency,
        quote_normalization_version: str,
        quote_revision_number: int,
        booked_amount: Money,
        booked_worst_case_amount: Money,
        commercial_change_ids: tuple[DisruptionCommercialChangeId, ...],
        opened_at: datetime,
        status: FinancialReconciliationStatus,
        current_invoice_revision_id: OperatorInvoiceRevisionId | None = None,
        current_dispute_id: ReconciliationDisputeId | None = None,
        current_variance_approval_id: VarianceApprovalId | None = None,
        final_invoice_revision_id: OperatorInvoiceRevisionId | None = None,
        approved_variance: Money | None = None,
        final_payable: Money | None = None,
        completed_at: datetime | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(reconciliation_id, version=version)
        if booked_amount.currency != currency or booked_worst_case_amount.currency != currency:
            raise DomainValidationError("booked reconciliation evidence must use one currency")
        if booked_amount.amount_minor <= 0:
            raise DomainValidationError("booked amount must be positive")
        if booked_worst_case_amount < booked_amount:
            raise DomainValidationError("booked worst-case amount cannot be below booked amount")
        if not isinstance(quote_revision_number, int) or isinstance(quote_revision_number, bool):
            raise DomainValidationError("quote_revision_number must be an integer")
        if quote_revision_number < 1:
            raise DomainValidationError("quote_revision_number must be positive")

        self.booking_id = booking_id
        self.accepted_quote_id = accepted_quote_id
        self.buyer_id = buyer_id
        self.operator_id = operator_id
        self.currency = currency
        self.quote_normalization_version = _required_text(
            quote_normalization_version,
            field_name="quote_normalization_version",
            max_length=64,
        )
        self.quote_revision_number = quote_revision_number
        self.booked_amount = booked_amount
        self.booked_worst_case_amount = booked_worst_case_amount
        self.opened_at = _utc(opened_at, field_name="opened_at")
        self.status = FinancialReconciliationStatus(status)
        self.current_invoice_revision_id = current_invoice_revision_id
        self.current_dispute_id = current_dispute_id
        self.current_variance_approval_id = current_variance_approval_id
        self.final_invoice_revision_id = final_invoice_revision_id
        self.approved_variance = approved_variance
        self.final_payable = final_payable
        self.completed_at = (
            _utc(completed_at, field_name="completed_at") if completed_at is not None else None
        )
        self._validate_state()

    def _validate_state(self) -> None:
        if self.approved_variance is not None and self.approved_variance.currency != self.currency:
            raise DomainValidationError("approved variance currency must match reconciliation")
        if self.final_payable is not None and self.final_payable.currency != self.currency:
            raise DomainValidationError("final payable currency must match reconciliation")

        if self.status is FinancialReconciliationStatus.OPEN:
            if any(
                value is not None
                for value in (
                    self.current_invoice_revision_id,
                    self.current_dispute_id,
                    self.current_variance_approval_id,
                )
            ):
                raise DomainValidationError("open reconciliation cannot contain invoice decisions")
        elif self.current_invoice_revision_id is None:
            raise DomainValidationError("active reconciliation requires a current invoice")

        if self.status is FinancialReconciliationStatus.INVOICE_SUBMITTED:
            if self.current_dispute_id is not None or self.current_variance_approval_id is not None:
                raise DomainValidationError("submitted invoice cannot already contain a decision")
        elif self.status is FinancialReconciliationStatus.DISPUTED and (
            self.current_dispute_id is None or self.current_variance_approval_id is not None
        ):
            raise DomainValidationError(
                "disputed reconciliation requires exact dispute evidence"
            )
        elif self.status is FinancialReconciliationStatus.VARIANCE_APPROVED and (
            self.current_variance_approval_id is None or self.current_dispute_id is not None
        ):
            raise DomainValidationError(
                "variance-approved reconciliation requires exact approval evidence"
            )

        if self.status is FinancialReconciliationStatus.COMPLETED:
            if (
                self.final_invoice_revision_id is None
                or self.approved_variance is None
                or self.final_payable is None
                or self.completed_at is None
                or self.current_dispute_id is not None
            ):
                raise DomainValidationError("completed reconciliation requires final evidence")
            if self.final_invoice_revision_id != self.current_invoice_revision_id:
                raise DomainValidationError(
                    "completed reconciliation must finalize current invoice"
                )
            if self.final_payable.amount_minor <= 0:
                raise DomainValidationError("final payable must be positive")
        elif any(
            value is not None
            for value in (
                self.final_invoice_revision_id,
                self.approved_variance,
                self.final_payable,
                self.completed_at,
            )
        ):
            raise DomainValidationError(
                "incomplete reconciliation cannot contain final settlement evidence"
            )

    @classmethod
    def open(
        cls,
        *,
        booking_id: BookingId,
        accepted_quote_id: QuoteId,
        buyer_id: OrganizationId,
        operator_id: OperatorId,
        currency: Currency,
        quote_normalization_version: str,
        quote_revision_number: int,
        booked_amount: Money,
        booked_worst_case_amount: Money,
        opened_at: datetime,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> FinancialReconciliation:
        reconciliation = cls(
            FinancialReconciliationId.new(),
            booking_id=booking_id,
            accepted_quote_id=accepted_quote_id,
            buyer_id=buyer_id,
            operator_id=operator_id,
            currency=currency,
            quote_normalization_version=quote_normalization_version,
            quote_revision_number=quote_revision_number,
            booked_amount=booked_amount,
            booked_worst_case_amount=booked_worst_case_amount,
            opened_at=opened_at,
            status=FinancialReconciliationStatus.OPEN,
        )
        reconciliation._record_event(
            "FINANCIAL_RECONCILIATION_OPENED",
            {
                "booking_id": str(booking_id),
                "accepted_quote_id": str(accepted_quote_id),
                "buyer_id": str(buyer_id),
                "operator_id": str(operator_id),
                "currency": str(currency),
                "quote_normalization_version": quote_normalization_version,
                "quote_revision_number": quote_revision_number,
                "booked_amount_minor": booked_amount.amount_minor,
                "booked_worst_case_amount_minor": booked_worst_case_amount.amount_minor,
                "commercial_change_ids": [str(item) for item in commercial_change_ids],
                "opened_at": _iso(reconciliation.opened_at),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=reconciliation.opened_at,
        )
        return reconciliation

    def record_invoice(
        self,
        invoice: OperatorInvoiceRevision,
        *,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if invoice.reconciliation_id != self.id:
            raise DomainValidationError("invoice revision belongs to another reconciliation")
        if invoice.currency != self.currency or invoice.booked_amount != self.booked_amount:
            raise DomainValidationError(
                "invoice booked-price evidence conflicts with reconciliation"
            )
        self.current_invoice_revision_id = invoice.id
        self.current_dispute_id = None
        self.current_variance_approval_id = None
        self.status = FinancialReconciliationStatus.INVOICE_SUBMITTED
        self._record_event(
            "OPERATOR_INVOICE_SUBMITTED",
            {
                "invoice_revision_id": str(invoice.id),
                "revision_number": invoice.revision_number,
                "supersedes_invoice_revision_id": (
                    str(invoice.supersedes_invoice_revision_id)
                    if invoice.supersedes_invoice_revision_id is not None
                    else None
                ),
                "invoice_reference": invoice.invoice_reference,
                "currency": str(invoice.currency),
                "booked_amount_minor": invoice.booked_amount.amount_minor,
                "invoice_total_minor": invoice.total_amount.amount_minor,
                "variance_minor": invoice.variance.amount_minor,
                "surcharge_reason": invoice.surcharge_reason,
                "line_items": [
                    {
                        "line_number": line.line_number,
                        "category": line.category.value,
                        "label": line.label,
                        "amount_minor": line.amount.amount_minor,
                        "reason": line.reason,
                    }
                    for line in invoice.line_items
                ],
                "submitted_at": _iso(invoice.submitted_at),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=invoice.submitted_at,
        )

    def record_invoice_superseded(
        self,
        invoice_revision_id: OperatorInvoiceRevisionId,
        *,
        replacement_invoice_revision_id: OperatorInvoiceRevisionId,
        superseded_at: datetime,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        when = _utc(superseded_at, field_name="superseded_at")
        self._record_event(
            "OPERATOR_INVOICE_SUPERSEDED",
            {
                "invoice_revision_id": str(invoice_revision_id),
                "replacement_invoice_revision_id": str(replacement_invoice_revision_id),
                "superseded_at": _iso(when),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_dispute(
        self,
        dispute: ReconciliationDispute,
        *,
        invoice: OperatorInvoiceRevision,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if dispute.reconciliation_id != self.id or invoice.reconciliation_id != self.id:
            raise DomainValidationError("dispute evidence belongs to another reconciliation")
        if (
            self.current_invoice_revision_id != invoice.id
            or dispute.invoice_revision_id != invoice.id
        ):
            raise DomainValidationError("dispute must target the exact current invoice revision")
        if dispute.buyer_id != self.buyer_id:
            raise DomainValidationError("dispute buyer does not own this reconciliation")
        if dispute.disputed_amount.currency != self.currency:
            raise DomainValidationError("disputed amount currency must match reconciliation")
        if dispute.disputed_amount > invoice.total_amount:
            raise DomainValidationError("disputed amount cannot exceed invoice total")
        self.current_dispute_id = dispute.id
        self.current_variance_approval_id = None
        self.status = FinancialReconciliationStatus.DISPUTED
        self._record_event(
            "FINANCIAL_RECONCILIATION_DISPUTED",
            {
                "dispute_id": str(dispute.id),
                "invoice_revision_id": str(invoice.id),
                "disputed_amount_minor": dispute.disputed_amount.amount_minor,
                "reason": dispute.reason,
                "opened_at": _iso(dispute.opened_at),
            },
            actor_id=dispute.buyer_id,
            correlation_id=correlation_id,
            occurred_at=dispute.opened_at,
        )

    def record_variance_approval(
        self,
        approval: VarianceApproval,
        *,
        invoice: OperatorInvoiceRevision,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if approval.reconciliation_id != self.id or invoice.reconciliation_id != self.id:
            raise DomainValidationError("variance approval belongs to another reconciliation")
        if (
            self.current_invoice_revision_id != invoice.id
            or approval.invoice_revision_id != invoice.id
        ):
            raise DomainValidationError(
                "variance approval must target the exact current invoice revision"
            )
        if approval.buyer_id != self.buyer_id:
            raise DomainValidationError("variance approval buyer does not own reconciliation")
        if approval.approved_variance.currency != self.currency:
            raise DomainValidationError("approved variance currency must match reconciliation")
        if self.current_dispute_id != approval.resolves_dispute_id:
            raise DomainValidationError(
                "variance approval must explicitly resolve the exact current dispute"
            )
        if invoice.variance.amount_minor <= 0:
            raise DomainValidationError("non-positive invoice variance requires no approval")
        if approval.approved_variance > invoice.variance:
            raise DomainValidationError("approved variance cannot exceed invoice variance")
        if approval.approved_at < invoice.submitted_at:
            raise DomainValidationError("variance approval cannot precede invoice submission")

        self.current_dispute_id = None
        self.current_variance_approval_id = approval.id
        self.status = FinancialReconciliationStatus.VARIANCE_APPROVED
        self._record_event(
            "FINANCIAL_VARIANCE_APPROVED",
            {
                "variance_approval_id": str(approval.id),
                "invoice_revision_id": str(invoice.id),
                "invoice_variance_minor": invoice.variance.amount_minor,
                "approved_variance_minor": approval.approved_variance.amount_minor,
                "resolves_dispute_id": (
                    str(approval.resolves_dispute_id)
                    if approval.resolves_dispute_id is not None
                    else None
                ),
                "approved_at": _iso(approval.approved_at),
                "note": approval.note,
            },
            actor_id=approval.buyer_id,
            correlation_id=correlation_id,
            occurred_at=approval.approved_at,
        )

    def complete(
        self,
        *,
        invoice: OperatorInvoiceRevision,
        approval: VarianceApproval | None,
        completed_at: datetime,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> Money:
        self._require_mutable()
        if self.current_invoice_revision_id != invoice.id or invoice.reconciliation_id != self.id:
            raise DomainValidationError("completion must use the exact current invoice revision")
        if self.current_dispute_id is not None:
            raise DomainValidationError("disputed invoice cannot complete reconciliation")
        when = _utc(completed_at, field_name="completed_at")
        if when < invoice.submitted_at:
            raise DomainValidationError("completion cannot precede invoice submission")

        approved_variance = Money.zero(self.currency)
        if invoice.variance.amount_minor > 0:
            if (
                approval is None
                or self.current_variance_approval_id != approval.id
                or approval.reconciliation_id != self.id
                or approval.invoice_revision_id != invoice.id
            ):
                raise DomainValidationError(
                    "positive invoice variance requires exact current buyer approval"
                )
            approved_variance = approval.approved_variance
            final_payable = self.booked_amount + approved_variance
        else:
            if approval is not None or self.current_variance_approval_id is not None:
                raise DomainValidationError(
                    "non-positive invoice variance cannot use variance approval"
                )
            final_payable = invoice.total_amount

        if final_payable > invoice.total_amount:
            raise DomainValidationError("final payable cannot exceed current invoice total")

        self.status = FinancialReconciliationStatus.COMPLETED
        self.final_invoice_revision_id = invoice.id
        self.approved_variance = approved_variance
        self.final_payable = final_payable
        self.completed_at = when
        self._record_event(
            "FINANCIAL_RECONCILIATION_COMPLETED",
            {
                "final_invoice_revision_id": str(invoice.id),
                "booked_amount_minor": self.booked_amount.amount_minor,
                "invoice_total_minor": invoice.total_amount.amount_minor,
                "invoice_variance_minor": invoice.variance.amount_minor,
                "approved_variance_minor": approved_variance.amount_minor,
                "final_payable_minor": final_payable.amount_minor,
                "currency": str(self.currency),
                "completed_at": _iso(when),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )
        return final_payable

    def _require_mutable(self) -> None:
        if self.status is FinancialReconciliationStatus.COMPLETED:
            raise DomainValidationError("completed financial reconciliation is terminal")
