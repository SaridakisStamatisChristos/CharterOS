from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import (
    DomainEventRepository,
    OperatorRepository,
    OrganizationRepository,
)
from charteros.application.ports.disruptions import DisruptionRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.reconciliation import FinancialReconciliationRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.domain.bookings import Booking, BookingId, BookingState
from charteros.domain.disruptions import DisruptionCommercialChangeId, DisruptionStatus
from charteros.domain.missions import Mission, MissionStatus
from charteros.domain.operators import CommercialStatus, OperatorId
from charteros.domain.organizations import OrganizationId, OrganizationStatus
from charteros.domain.quotes import Quote, QuoteStatus
from charteros.domain.quotes.normalization import QuoteNormalization, normalize_quote
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
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money

MAX_INVOICE_REVISIONS = 100


@dataclass(frozen=True, slots=True)
class ReconciliationPartyContext:
    buyer_id: OrganizationId | None = None
    operator_id: OperatorId | None = None

    def __post_init__(self) -> None:
        if (self.buyer_id is None) == (self.operator_id is None):
            raise DomainValidationError(
                "exactly one buyer or operator reconciliation context is required"
            )

    @property
    def actor_id(self) -> TypedId:
        if self.buyer_id is not None:
            return self.buyer_id
        assert self.operator_id is not None
        return self.operator_id


@dataclass(frozen=True, slots=True)
class InvoiceLineInput:
    category: InvoiceLineCategory
    label: str
    amount_minor: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _BookingContext:
    booking: Booking
    mission: Mission
    accepted_quote: Quote


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class FinancialReconciliationService:
    """Canonical post-operation financial settlement over immutable booked evidence."""

    def __init__(
        self,
        *,
        reconciliations: FinancialReconciliationRepository,
        disruptions: DisruptionRepository,
        bookings: BookingRepository,
        missions: MissionRepository,
        quotes: QuoteRepository,
        rfqs: RfqRepository,
        organizations: OrganizationRepository,
        operators: OperatorRepository,
        events: DomainEventRepository,
    ) -> None:
        self._reconciliations = reconciliations
        self._disruptions = disruptions
        self._bookings = bookings
        self._missions = missions
        self._quotes = quotes
        self._rfqs = rfqs
        self._organizations = organizations
        self._operators = operators
        self._events = events

    def open_reconciliation(
        self,
        *,
        booking_id: BookingId,
        operator_id: OperatorId,
        opened_at: datetime,
        correlation_id: CorrelationId,
    ) -> FinancialReconciliation:
        when = _utc(opened_at, field_name="opened_at")
        context = self._booking_context(booking_id, for_update=True)
        self._assert_operator(context, operator_id)
        self._assert_reconcilable_booking(context)

        existing = self._reconciliations.get_for_booking(booking_id)
        if existing is not None:
            raise EntityConflictError("booking already has a financial reconciliation")

        (
            normalization,
            booked_amount,
            booked_worst_case_amount,
            commercial_change_ids,
        ) = self._booked_commercial_baseline(context)
        reconciliation = FinancialReconciliation.open(
            booking_id=context.booking.id,
            accepted_quote_id=context.accepted_quote.id,
            buyer_id=context.mission.buyer_id,
            operator_id=context.booking.operator_id,
            currency=context.accepted_quote.currency,
            quote_normalization_version=normalization.normalization_version,
            quote_revision_number=context.accepted_quote.revision_number,
            booked_amount=booked_amount,
            booked_worst_case_amount=booked_worst_case_amount,
            commercial_change_ids=commercial_change_ids,
            opened_at=max(when, context.booking.state_changed_at),
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        self._reconciliations.add(reconciliation)
        self._events.add_aggregate_events(reconciliation)
        return reconciliation

    def get(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        party: ReconciliationPartyContext,
    ) -> FinancialReconciliation:
        reconciliation = self._reconciliations.get(reconciliation_id)
        if reconciliation is None:
            raise EntityNotFoundError("financial reconciliation does not exist")
        context = self._booking_context(reconciliation.booking_id, for_update=False)
        self._assert_party(context, party)
        self._assert_reconciliation_lineage(reconciliation, context)
        return reconciliation

    def get_for_booking(
        self,
        *,
        booking_id: BookingId,
        party: ReconciliationPartyContext,
    ) -> FinancialReconciliation:
        context = self._booking_context(booking_id, for_update=False)
        self._assert_party(context, party)
        reconciliation = self._reconciliations.get_for_booking(booking_id)
        if reconciliation is None:
            raise EntityNotFoundError("booking does not have a financial reconciliation")
        self._assert_reconciliation_lineage(reconciliation, context)
        return reconciliation

    def submit_invoice(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        operator_id: OperatorId,
        invoice_reference: str,
        currency: Currency,
        total_amount_minor: int,
        line_items: tuple[InvoiceLineInput, ...],
        surcharge_reason: str | None,
        submitted_at: datetime,
        correlation_id: CorrelationId,
    ) -> OperatorInvoiceRevision:
        when = _utc(submitted_at, field_name="submitted_at")
        reconciliation, context = self._locked_mutation_context(reconciliation_id)
        self._assert_operator(context, operator_id)
        self._assert_reconciliation_lineage(reconciliation, context)
        self._assert_reconciliation_mutable(reconciliation)
        self._assert_reconcilable_booking(context)

        if currency != reconciliation.currency:
            raise EntityConflictError(
                "operator invoice must use the accepted quote currency; implicit FX is unavailable"
            )
        current = self._reconciliations.get_current_invoice_for_update(reconciliation.id)
        if current is not None and when < current.submitted_at:
            raise EntityConflictError("revised invoice cannot be backdated before current invoice")

        lines = tuple(
            InvoiceLine(
                line_number=index,
                category=item.category,
                label=item.label,
                amount=Money(item.amount_minor, currency),
                reason=item.reason,
            )
            for index, item in enumerate(line_items, start=1)
        )
        invoice = OperatorInvoiceRevision(
            id=OperatorInvoiceRevisionId.new(),
            reconciliation_id=reconciliation.id,
            revision_number=current.revision_number + 1 if current is not None else 1,
            supersedes_invoice_revision_id=current.id if current is not None else None,
            status=InvoiceRevisionStatus.CURRENT,
            invoice_reference=invoice_reference,
            currency=currency,
            booked_amount=reconciliation.booked_amount,
            line_items=lines,
            total_amount=Money(total_amount_minor, currency),
            variance=Money(total_amount_minor, currency) - reconciliation.booked_amount,
            surcharge_reason=surcharge_reason,
            submitted_at=when,
        )

        expected_version = reconciliation.version
        if current is not None:
            self._reconciliations.supersede_invoice(current.id, superseded_at=when)
            reconciliation.record_invoice_superseded(
                current.id,
                replacement_invoice_revision_id=invoice.id,
                superseded_at=when,
                actor_id=operator_id,
                correlation_id=correlation_id,
            )

        self._reconciliations.add_invoice(invoice)
        reconciliation.record_invoice(
            invoice,
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        self._reconciliations.save(reconciliation, expected_version=expected_version)
        self._events.add_aggregate_events(reconciliation)
        return invoice

    def list_invoices(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        party: ReconciliationPartyContext,
        limit: int,
    ) -> tuple[OperatorInvoiceRevision, ...]:
        self.get(reconciliation_id=reconciliation_id, party=party)
        bounded = min(max(limit, 1), MAX_INVOICE_REVISIONS)
        return self._reconciliations.list_invoices(reconciliation_id, limit=bounded)

    def dispute_invoice(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        buyer_id: OrganizationId,
        disputed_amount_minor: int,
        reason: str,
        opened_at: datetime,
        correlation_id: CorrelationId,
    ) -> ReconciliationDispute:
        when = _utc(opened_at, field_name="opened_at")
        reconciliation, context = self._locked_mutation_context(reconciliation_id)
        self._assert_buyer(context, buyer_id)
        self._assert_reconciliation_lineage(reconciliation, context)
        self._assert_reconciliation_mutable(reconciliation)

        invoice = self._reconciliations.get_current_invoice_for_update(reconciliation.id)
        if invoice is None or reconciliation.current_invoice_revision_id != invoice.id:
            raise EntityConflictError("financial reconciliation has no current invoice")
        if reconciliation.current_dispute_id is not None:
            raise EntityConflictError("current invoice is already disputed")
        if reconciliation.current_variance_approval_id is not None:
            raise EntityConflictError("current invoice variance is already approved")
        if when < invoice.submitted_at:
            raise EntityConflictError("dispute cannot precede invoice submission")

        dispute = ReconciliationDispute(
            id=ReconciliationDisputeId.new(),
            reconciliation_id=reconciliation.id,
            invoice_revision_id=invoice.id,
            buyer_id=buyer_id,
            disputed_amount=Money(disputed_amount_minor, reconciliation.currency),
            reason=reason,
            opened_at=when,
        )
        expected_version = reconciliation.version
        self._reconciliations.add_dispute(dispute)
        reconciliation.record_dispute(
            dispute,
            invoice=invoice,
            correlation_id=correlation_id,
        )
        self._reconciliations.save(reconciliation, expected_version=expected_version)
        self._events.add_aggregate_events(reconciliation)
        return dispute

    def approve_variance(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        buyer_id: OrganizationId,
        approved_variance_minor: int,
        resolves_dispute_id: ReconciliationDisputeId | None,
        approved_at: datetime,
        note: str | None,
        correlation_id: CorrelationId,
    ) -> VarianceApproval:
        when = _utc(approved_at, field_name="approved_at")
        reconciliation, context = self._locked_mutation_context(reconciliation_id)
        self._assert_buyer(context, buyer_id)
        self._assert_reconciliation_lineage(reconciliation, context)
        self._assert_reconciliation_mutable(reconciliation)

        invoice = self._reconciliations.get_current_invoice_for_update(reconciliation.id)
        if invoice is None or reconciliation.current_invoice_revision_id != invoice.id:
            raise EntityConflictError("financial reconciliation has no current invoice")
        if reconciliation.current_variance_approval_id is not None:
            raise EntityConflictError("current invoice already has variance approval")
        if reconciliation.current_dispute_id != resolves_dispute_id:
            raise EntityConflictError(
                "variance approval must explicitly resolve the exact current dispute"
            )
        if invoice.variance.amount_minor <= 0:
            raise EntityConflictError("current invoice has no positive variance to approve")

        approval = VarianceApproval(
            id=VarianceApprovalId.new(),
            reconciliation_id=reconciliation.id,
            invoice_revision_id=invoice.id,
            buyer_id=buyer_id,
            approved_variance=Money(approved_variance_minor, reconciliation.currency),
            resolves_dispute_id=resolves_dispute_id,
            approved_at=when,
            note=note,
        )
        if approval.approved_variance > invoice.variance:
            raise EntityConflictError("approved variance cannot exceed current invoice variance")

        expected_version = reconciliation.version
        self._reconciliations.add_variance_approval(approval)
        reconciliation.record_variance_approval(
            approval,
            invoice=invoice,
            correlation_id=correlation_id,
        )
        self._reconciliations.save(reconciliation, expected_version=expected_version)
        self._events.add_aggregate_events(reconciliation)
        return approval

    def complete_reconciliation(
        self,
        *,
        reconciliation_id: FinancialReconciliationId,
        operator_id: OperatorId,
        completed_at: datetime,
        correlation_id: CorrelationId,
    ) -> FinancialReconciliation:
        when = _utc(completed_at, field_name="completed_at")

        observed = self._reconciliations.get(reconciliation_id)
        if observed is None:
            raise EntityNotFoundError("financial reconciliation does not exist")

        context = self._booking_context(observed.booking_id, for_update=True)
        self._assert_operator(context, operator_id)
        self._assert_reconcilable_booking(context)

        reconciliation = self._reconciliations.get_for_update(reconciliation_id)
        if reconciliation is None:
            raise EntityNotFoundError("financial reconciliation does not exist")
        self._assert_reconciliation_lineage(reconciliation, context)
        self._assert_reconciliation_mutable(reconciliation)

        invoice = self._reconciliations.get_current_invoice_for_update(reconciliation.id)
        if invoice is None or reconciliation.current_invoice_revision_id != invoice.id:
            raise EntityConflictError("financial reconciliation has no current invoice")
        if reconciliation.current_dispute_id is not None:
            raise EntityConflictError("disputed invoice cannot complete reconciliation")

        approval = None
        if reconciliation.current_variance_approval_id is not None:
            approval = self._reconciliations.get_variance_approval(
                reconciliation.current_variance_approval_id
            )
            if approval is None:
                raise EntityConflictError("variance approval evidence is missing")
        if invoice.variance.amount_minor > 0 and approval is None:
            raise EntityConflictError("positive invoice variance requires buyer approval")

        expected_reconciliation_version = reconciliation.version
        expected_booking_version = context.booking.version
        reconciliation.complete(
            invoice=invoice,
            approval=approval,
            completed_at=max(when, context.booking.state_changed_at),
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        context.booking.reconcile(
            transitioned_at=max(when, context.booking.state_changed_at),
            correlation_id=correlation_id,
        )
        self._reconciliations.save(
            reconciliation,
            expected_version=expected_reconciliation_version,
        )
        self._bookings.save(context.booking, expected_version=expected_booking_version)
        self._events.add_aggregate_events(reconciliation)
        self._events.add_aggregate_events(context.booking)
        return reconciliation

    def _locked_mutation_context(
        self,
        reconciliation_id: FinancialReconciliationId,
    ) -> tuple[FinancialReconciliation, _BookingContext]:
        observed = self._reconciliations.get(reconciliation_id)
        if observed is None:
            raise EntityNotFoundError("financial reconciliation does not exist")
        context = self._booking_context(observed.booking_id, for_update=True)
        reconciliation = self._reconciliations.get_for_update(reconciliation_id)
        if reconciliation is None:
            raise EntityNotFoundError("financial reconciliation does not exist")
        if reconciliation.booking_id != observed.booking_id:
            raise EntityConflictError("financial reconciliation booking lineage changed")
        return reconciliation, context

    def _booking_context(
        self,
        booking_id: BookingId,
        *,
        for_update: bool,
    ) -> _BookingContext:
        booking = (
            self._bookings.get_for_update(booking_id)
            if for_update
            else self._bookings.get(booking_id)
        )
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        mission = self._missions.get(booking.mission_id)
        if mission is None:
            raise EntityConflictError("booking mission lineage is incomplete")
        quote = self._quotes.get(booking.accepted_quote_id)
        if quote is None:
            raise EntityConflictError("booking accepted quote lineage is incomplete")
        rfq = self._rfqs.get(quote.rfq_id)
        if (
            quote.id != booking.accepted_quote_id
            or quote.status is not QuoteStatus.ACCEPTED
            or quote.aircraft_id != booking.aircraft_id
            or rfq is None
            or rfq.mission_id != booking.mission_id
            or rfq.operator_id != booking.operator_id
        ):
            raise EntityConflictError(
                "booking/mission/quote/operator/aircraft lineage is inconsistent"
            )
        return _BookingContext(booking=booking, mission=mission, accepted_quote=quote)

    def _assert_party(
        self,
        context: _BookingContext,
        party: ReconciliationPartyContext,
    ) -> None:
        if party.buyer_id is not None:
            self._assert_buyer(context, party.buyer_id)
            return
        assert party.operator_id is not None
        self._assert_operator(context, party.operator_id)

    def _assert_buyer(
        self,
        context: _BookingContext,
        buyer_id: OrganizationId,
    ) -> None:
        buyer = self._organizations.get(buyer_id)
        if (
            buyer is None
            or buyer.status is not OrganizationStatus.ACTIVE
            or context.mission.buyer_id != buyer_id
        ):
            raise EntityNotFoundError(
                "financial reconciliation is not available in the buyer context"
            )

    def _assert_operator(
        self,
        context: _BookingContext,
        operator_id: OperatorId,
    ) -> None:
        operator = self._operators.get(operator_id)
        if (
            operator is None
            or operator.commercial_status is not CommercialStatus.ACTIVE
            or context.booking.operator_id != operator_id
        ):
            raise EntityNotFoundError(
                "financial reconciliation is not available in the operator context"
            )

    def _booked_commercial_baseline(
        self,
        context: _BookingContext,
    ) -> tuple[
        QuoteNormalization,
        Money,
        Money,
        tuple[DisruptionCommercialChangeId, ...],
    ]:
        normalization = normalize_quote(context.accepted_quote)
        booked_amount = normalization.expected_total
        booked_worst_case_amount = normalization.worst_case_total
        commercial_change_ids: list[DisruptionCommercialChangeId] = []

        disruptions = self._disruptions.list_for_booking(
            context.booking.id,
            limit=1001,
        )
        if len(disruptions) > 1000:
            raise EntityConflictError(
                "too many disruptions to establish a bounded reconciliation baseline"
            )
        for disruption in disruptions:
            if disruption.status is not DisruptionStatus.RESOLVED:
                raise EntityConflictError(
                    "all booking disruptions must be resolved before financial reconciliation"
                )
            if disruption.selected_commercial_change_id is None:
                continue
            change = self._disruptions.get_commercial_change(
                disruption.selected_commercial_change_id
            )
            if (
                change is None
                or change.disruption_id != disruption.id
                or change.original_quote_id != context.accepted_quote.id
                or change.currency != context.accepted_quote.currency
                or change.normalization_version != normalization.normalization_version
            ):
                raise EntityConflictError(
                    "resolved disruption commercial evidence conflicts with booked quote"
                )
            booked_amount = booked_amount + change.known_adjustment
            booked_worst_case_amount = (
                booked_worst_case_amount
                + change.known_adjustment
                + change.conditional_adjustment
            )
            commercial_change_ids.append(change.id)

        if booked_amount.amount_minor <= 0 or booked_worst_case_amount < booked_amount:
            raise EntityConflictError(
                "resolved commercial adjustments produce an invalid booked-price baseline"
            )
        return (
            normalization,
            booked_amount,
            booked_worst_case_amount,
            tuple(commercial_change_ids),
        )

    @staticmethod
    def _assert_reconcilable_booking(context: _BookingContext) -> None:
        if context.booking.state is not BookingState.COMPLETED:
            raise EntityConflictError("financial reconciliation requires a completed booking")
        if context.mission.status is not MissionStatus.COMPLETED:
            raise EntityConflictError("financial reconciliation requires a completed mission")

    @staticmethod
    def _assert_reconciliation_lineage(
        reconciliation: FinancialReconciliation,
        context: _BookingContext,
    ) -> None:
        if (
            reconciliation.booking_id != context.booking.id
            or reconciliation.accepted_quote_id != context.booking.accepted_quote_id
            or reconciliation.buyer_id != context.mission.buyer_id
            or reconciliation.operator_id != context.booking.operator_id
            or reconciliation.currency != context.accepted_quote.currency
            or reconciliation.quote_revision_number != context.accepted_quote.revision_number
        ):
            raise EntityConflictError(
                "financial reconciliation conflicts with canonical booking evidence"
            )

    @staticmethod
    def _assert_reconciliation_mutable(reconciliation: FinancialReconciliation) -> None:
        if reconciliation.status is FinancialReconciliationStatus.COMPLETED:
            raise EntityConflictError("completed financial reconciliation is terminal")
