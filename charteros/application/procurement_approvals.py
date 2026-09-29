from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.bookings import BookingService
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import DomainEventRepository, OrganizationRepository
from charteros.application.ports.contracts import ContractRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.procurement_approvals import ProcurementApprovalRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.application.ports.tenders import TenderRepository
from charteros.domain.bookings import Booking
from charteros.domain.missions import MissionId, MissionStatus
from charteros.domain.organizations import OrganizationId, OrganizationStatus
from charteros.domain.procurement_approvals import (
    ProcurementApproval,
    ProcurementApprovalId,
    ProcurementApprovalStatus,
)
from charteros.domain.quotes import QuoteId, QuoteStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class ProcurementApprovalService:
    """Buyer approval evidence; BookingService remains the award authority."""

    def __init__(
        self,
        *,
        approvals: ProcurementApprovalRepository,
        organizations: OrganizationRepository,
        missions: MissionRepository,
        rfqs: RfqRepository,
        quotes: QuoteRepository,
        bookings: BookingRepository,
        contracts: ContractRepository,
        tenders: TenderRepository,
        events: DomainEventRepository,
    ) -> None:
        self._approvals = approvals
        self._organizations = organizations
        self._missions = missions
        self._rfqs = rfqs
        self._quotes = quotes
        self._bookings = bookings
        self._contracts = contracts
        self._tenders = tenders
        self._events = events

    def approve(
        self,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
        quote_id: QuoteId,
        approved_at: datetime,
        note: str | None,
        correlation_id: CorrelationId,
    ) -> ProcurementApproval:
        when = _utc(approved_at, field_name="approved_at")
        self._assert_buyer(buyer_id)

        mission = self._missions.get_for_update(mission_id)
        if mission is None or mission.buyer_id != buyer_id:
            raise EntityNotFoundError("mission is not available in the buyer context")
        if mission.status not in (MissionStatus.SOURCING, MissionStatus.QUOTED):
            raise EntityConflictError("mission is not in an approvable procurement state")

        quote = self._quotes.get_for_update(quote_id)
        if quote is None:
            raise EntityNotFoundError("quote is not available in the buyer context")
        rfq = self._rfqs.get(quote.rfq_id)
        if rfq is None or rfq.mission_id != mission.id:
            raise EntityNotFoundError("quote is not available in the buyer context")
        if self._tenders.find_invitation_for_rfq(rfq.id) is not None:
            raise EntityConflictError(
                "tender bids must be approved and awarded through the tender workflow"
            )
        if quote.status is not QuoteStatus.SUBMITTED or not quote.is_current:
            raise EntityConflictError("only the current submitted quote revision can be approved")
        if when < quote.submitted_at or when >= quote.valid_until:
            raise EntityConflictError("quote can only be approved within its validity window")

        current = self._approvals.get_current_for_mission_for_update(mission.id)
        if current is not None and current.quote_id == quote.id:
            return current

        replacement = ProcurementApproval.create(
            mission_id=mission.id,
            buyer_id=buyer_id,
            quote_id=quote.id,
            approved_at=when,
            note=note,
            supersedes_approval_id=current.id if current is not None else None,
            correlation_id=correlation_id,
        )
        if current is not None:
            expected_version = current.version
            current.supersede(
                replacement_approval_id=replacement.id,
                superseded_at=when,
                correlation_id=correlation_id,
            )
            self._approvals.save(current, expected_version=expected_version)
            self._events.add_aggregate_events(current)

        self._approvals.add(replacement)
        self._events.add_aggregate_events(replacement)
        return replacement

    def award(
        self,
        *,
        buyer_id: OrganizationId,
        approval_id: ProcurementApprovalId,
        awarded_at: datetime,
        correlation_id: CorrelationId,
    ) -> tuple[ProcurementApproval, Booking]:
        when = _utc(awarded_at, field_name="awarded_at")
        self._assert_buyer(buyer_id)

        observed = self._approvals.get(approval_id)
        if observed is None or observed.buyer_id != buyer_id:
            raise EntityNotFoundError("approval is not available in the buyer context")

        mission = self._missions.get_for_update(observed.mission_id)
        if mission is None or mission.buyer_id != buyer_id:
            raise EntityNotFoundError("approval mission is not available in the buyer context")

        approval = self._approvals.get_for_update(approval_id)
        if approval is None or approval.buyer_id != buyer_id:
            raise EntityNotFoundError("approval is not available in the buyer context")
        if approval.status is not ProcurementApprovalStatus.APPROVED:
            raise EntityConflictError("only an active procurement approval can be awarded")

        quote = self._quotes.get(approval.quote_id)
        if quote is None:
            raise EntityNotFoundError("approved quote does not exist")
        rfq = self._rfqs.get(quote.rfq_id)
        if rfq is None or rfq.mission_id != mission.id:
            raise EntityConflictError("approved quote no longer belongs to the approval mission")
        if self._tenders.find_invitation_for_rfq(rfq.id) is not None:
            raise EntityConflictError("tender bids must be awarded through the tender workflow")
        if quote.status is not QuoteStatus.SUBMITTED or not quote.is_current:
            raise EntityConflictError(
                "approved quote revision is stale; approve the current submitted revision"
            )
        if when < quote.submitted_at or when >= quote.valid_until:
            raise EntityConflictError("approved quote is no longer within its validity window")

        booking = BookingService(
            bookings=self._bookings,
            quotes=self._quotes,
            rfqs=self._rfqs,
            missions=self._missions,
            events=self._events,
            contracts=self._contracts,
            tenders=self._tenders,
        ).accept_quote(
            quote_id=approval.quote_id,
            now=when,
            correlation_id=correlation_id,
        )
        expected_version = approval.version
        approval.consume(
            booking_id=booking.id,
            consumed_at=when,
            correlation_id=correlation_id,
        )
        self._approvals.save(approval, expected_version=expected_version)
        self._events.add_aggregate_events(approval)
        return approval, booking

    def get(
        self,
        *,
        buyer_id: OrganizationId,
        approval_id: ProcurementApprovalId,
    ) -> ProcurementApproval:
        self._assert_buyer(buyer_id)
        approval = self._approvals.get(approval_id)
        if approval is None or approval.buyer_id != buyer_id:
            raise EntityNotFoundError("approval is not available in the buyer context")
        return approval

    def _assert_buyer(self, buyer_id: OrganizationId) -> None:
        buyer = self._organizations.get(buyer_id)
        if buyer is None or buyer.status is not OrganizationStatus.ACTIVE:
            raise EntityNotFoundError("buyer context does not exist or is not active")
