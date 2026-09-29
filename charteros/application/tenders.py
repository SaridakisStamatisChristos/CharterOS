from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from charteros.application.bookings import BookingService
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import (
    AircraftRepository,
    DomainEventRepository,
    OperatorRepository,
)
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.application.ports.tenders import TenderAuditEvent, TenderRepository
from charteros.application.quotes import QuoteService
from charteros.application.rfqs import RfqService
from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import Booking
from charteros.domain.missions import MissionId, MissionStatus
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import PriceComponent, Quote, QuoteId, QuoteStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId
from charteros.domain.shared.money import Money
from charteros.domain.tenders import (
    Tender,
    TenderActorId,
    TenderAdminCorrection,
    TenderAdminCorrectionId,
    TenderId,
    TenderInvitation,
    TenderInvitationId,
    TenderInvitationStatus,
    TenderStatus,
)


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TenderSupplierView:
    tender: Tender
    invitation: TenderInvitation
    quotes: tuple[Quote, ...]


@dataclass(frozen=True, slots=True)
class TenderAuditTrail:
    tender: Tender
    invitations: tuple[TenderInvitation, ...]
    corrections: tuple[TenderAdminCorrection, ...]
    events: tuple[TenderAuditEvent, ...]


class TenderService:
    def __init__(
        self,
        *,
        tenders: TenderRepository,
        missions: MissionRepository,
        operators: OperatorRepository,
        rfqs: RfqRepository,
        quotes: QuoteRepository,
        aircraft: AircraftRepository,
        bookings: BookingRepository,
        events: DomainEventRepository,
    ) -> None:
        self._tenders = tenders
        self._missions = missions
        self._operators = operators
        self._rfqs = rfqs
        self._quotes = quotes
        self._aircraft = aircraft
        self._bookings = bookings
        self._events = events

    def create(
        self,
        *,
        mission_id: MissionId,
        opens_at: datetime,
        deadline_at: datetime,
        sealed_bid: bool,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Tender:
        created_at = _utc(now, field_name="now")
        opens = _utc(opens_at, field_name="opens_at")
        deadline = _utc(deadline_at, field_name="deadline_at")
        mission = self._missions.get_for_update(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")
        if mission.status is not MissionStatus.OPEN:
            raise EntityConflictError(
                "tender can only be created for an open mission before generic sourcing begins"
            )
        if created_at >= deadline:
            raise EntityConflictError("tender deadline must be in the future")
        if opens >= deadline:
            raise EntityConflictError("tender opens_at must precede deadline")
        if deadline >= mission.departure_window.start:
            raise EntityConflictError("tender deadline must precede mission departure")
        if self._tenders.get_for_mission(mission_id) is not None:
            raise EntityConflictError("mission already has a tender")

        tender = Tender.create(
            mission_id=mission_id,
            sealed_bid=sealed_bid,
            opens_at=opens,
            deadline_at=deadline,
            created_at=created_at,
            correlation_id=correlation_id,
        )
        if created_at >= opens:
            tender.open(opened_at=created_at, correlation_id=correlation_id)
        self._tenders.add(tender)
        self._events.add_aggregate_events(tender)
        return tender

    def open(
        self,
        *,
        tender_id: TenderId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Tender:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        expected_version = tender.version
        tender.open(opened_at=when, correlation_id=correlation_id)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return tender

    def invite(
        self,
        *,
        tender_id: TenderId,
        operator_id: OperatorId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> TenderInvitation:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        if tender.status not in (TenderStatus.DRAFT, TenderStatus.OPEN):
            raise EntityConflictError("new suppliers cannot be invited after best-and-final starts")
        if when >= tender.deadline_at:
            raise EntityConflictError("tender deadline has passed")
        if self._tenders.find_invitation_for_operator(tender.id, operator_id) is not None:
            raise EntityConflictError("operator is already invited to this tender")

        rfq = self._rfq_service().create_and_send(
            mission_id=tender.mission_id,
            operator_id=operator_id,
            response_deadline=tender.deadline_at,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        invitation = TenderInvitation(
            id=TenderInvitationId.new(),
            tender_id=tender.id,
            operator_id=operator_id,
            rfq_id=rfq.id,
            status=TenderInvitationStatus.INVITED,
            invited_at=when,
        )
        expected_version = tender.version
        tender.record_invitation(
            invitation_id=invitation.id,
            operator_id=operator_id,
            rfq_id=rfq.id,
            invited_at=when,
            correlation_id=correlation_id,
        )
        self._tenders.add_invitation(invitation)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return invitation

    def accept_invitation(
        self,
        *,
        invitation_id: TenderInvitationId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> TenderInvitation:
        when = _utc(now, field_name="now")
        invitation = self._get_invitation_for_update(invitation_id)
        if invitation.status is not TenderInvitationStatus.INVITED:
            raise EntityConflictError("only an unanswered tender invitation can be accepted")
        tender = self._get_for_update(invitation.tender_id)
        if tender.status is not TenderStatus.OPEN:
            raise EntityConflictError("tender must be open before an invitation can be accepted")
        if when >= tender.deadline_at:
            raise EntityConflictError("tender deadline has passed")

        self._rfq_service().acknowledge(
            rfq_id=invitation.rfq_id,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        accepted = TenderInvitation(
            id=invitation.id,
            tender_id=invitation.tender_id,
            operator_id=invitation.operator_id,
            rfq_id=invitation.rfq_id,
            status=TenderInvitationStatus.ACCEPTED,
            invited_at=invitation.invited_at,
            responded_at=when,
            last_quote_id=invitation.last_quote_id,
            best_and_final_quote_id=invitation.best_and_final_quote_id,
        )
        expected_version = tender.version
        tender.record_invitation_response(
            invitation_id=accepted.id,
            operator_id=accepted.operator_id,
            accepted=True,
            responded_at=when,
            correlation_id=correlation_id,
        )
        self._tenders.save_invitation(accepted)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return accepted

    def decline_invitation(
        self,
        *,
        invitation_id: TenderInvitationId,
        reason: str | None,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> TenderInvitation:
        when = _utc(now, field_name="now")
        invitation = self._get_invitation_for_update(invitation_id)
        if invitation.status is not TenderInvitationStatus.INVITED:
            raise EntityConflictError("only an unanswered tender invitation can be declined")
        tender = self._get_for_update(invitation.tender_id)
        if tender.status not in (TenderStatus.OPEN, TenderStatus.BEST_AND_FINAL):
            raise EntityConflictError("tender is not accepting supplier responses")
        if when >= tender.deadline_at:
            raise EntityConflictError("tender deadline has passed")

        self._rfq_service().decline(
            rfq_id=invitation.rfq_id,
            reason=reason,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        declined = TenderInvitation(
            id=invitation.id,
            tender_id=invitation.tender_id,
            operator_id=invitation.operator_id,
            rfq_id=invitation.rfq_id,
            status=TenderInvitationStatus.DECLINED,
            invited_at=invitation.invited_at,
            responded_at=when,
        )
        expected_version = tender.version
        tender.record_invitation_response(
            invitation_id=declined.id,
            operator_id=declined.operator_id,
            accepted=False,
            responded_at=when,
            correlation_id=correlation_id,
        )
        self._tenders.save_invitation(declined)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return declined

    def submit_bid(
        self,
        *,
        invitation_id: TenderInvitationId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        invitation, tender = self._lock_participation(invitation_id, now=when)
        if tender.status is not TenderStatus.OPEN:
            raise EntityConflictError("ordinary bids are accepted only during the open phase")
        if invitation.last_quote_id is not None:
            raise EntityConflictError("initial bid already exists; submit a revision instead")
        self._validate_tender_quote_validity(tender=tender, valid_until=valid_until)

        quote = self._quote_service().submit(
            rfq_id=invitation.rfq_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid_until,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        updated = self._invitation_with_quote(invitation, quote, best_and_final=False)
        expected_version = tender.version
        tender.record_bid(
            invitation_id=invitation.id,
            operator_id=invitation.operator_id,
            quote_id=quote.id,
            revision_number=quote.revision_number,
            submitted_at=when,
            best_and_final=False,
            correlation_id=correlation_id,
        )
        self._tenders.save_invitation(updated)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return quote

    def revise_bid(
        self,
        *,
        invitation_id: TenderInvitationId,
        quote_id: QuoteId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        invitation, tender = self._lock_participation(invitation_id, now=when)
        if tender.status is not TenderStatus.OPEN:
            raise EntityConflictError(
                "ordinary revisions stop when best-and-final begins; use best-and-final submission"
            )
        self._assert_latest_quote(invitation, quote_id)
        self._validate_tender_quote_validity(tender=tender, valid_until=valid_until)

        quote = self._quote_service().revise(
            quote_id=quote_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid_until,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        updated = self._invitation_with_quote(invitation, quote, best_and_final=False)
        expected_version = tender.version
        tender.record_bid(
            invitation_id=invitation.id,
            operator_id=invitation.operator_id,
            quote_id=quote.id,
            revision_number=quote.revision_number,
            submitted_at=when,
            best_and_final=False,
            correlation_id=correlation_id,
        )
        self._tenders.save_invitation(updated)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return quote

    def request_best_and_final(
        self,
        *,
        tender_id: TenderId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Tender:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        if tender.status is not TenderStatus.OPEN:
            raise EntityConflictError("best-and-final can only start from an open tender")
        if when >= tender.deadline_at:
            raise EntityConflictError("tender deadline has passed")
        invitations = self._tenders.list_invitations(tender.id)
        has_bid = any(
            invitation.status is TenderInvitationStatus.ACCEPTED
            and invitation.last_quote_id is not None
            for invitation in invitations
        )
        if not has_bid:
            raise EntityConflictError("best-and-final requires at least one submitted bid")
        expected_version = tender.version
        tender.request_best_and_final(requested_at=when, correlation_id=correlation_id)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return tender

    def submit_best_and_final(
        self,
        *,
        invitation_id: TenderInvitationId,
        quote_id: QuoteId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        invitation, tender = self._lock_participation(invitation_id, now=when)
        if tender.status is not TenderStatus.BEST_AND_FINAL:
            raise EntityConflictError("tender is not in best-and-final phase")
        if invitation.best_and_final_quote_id is not None:
            raise EntityConflictError("best-and-final submission is already recorded")
        self._assert_latest_quote(invitation, quote_id)
        self._validate_tender_quote_validity(tender=tender, valid_until=valid_until)

        quote = self._quote_service().revise(
            quote_id=quote_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid_until,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        updated = self._invitation_with_quote(invitation, quote, best_and_final=True)
        expected_version = tender.version
        tender.record_bid(
            invitation_id=invitation.id,
            operator_id=invitation.operator_id,
            quote_id=quote.id,
            revision_number=quote.revision_number,
            submitted_at=when,
            best_and_final=True,
            correlation_id=correlation_id,
        )
        self._tenders.save_invitation(updated)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return quote

    def withdraw_bid(
        self,
        *,
        invitation_id: TenderInvitationId,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        invitation, tender = self._lock_participation(invitation_id, now=when)
        self._assert_latest_quote(invitation, quote_id)
        quote = self._quote_service().withdraw(
            quote_id=quote_id,
            now=when,
            correlation_id=correlation_id,
            tender_command=True,
        )
        expected_version = tender.version
        tender.record_bid_withdrawal(
            invitation_id=invitation.id,
            operator_id=invitation.operator_id,
            quote_id=quote.id,
            withdrawn_at=when,
            correlation_id=correlation_id,
        )
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return quote

    def close(
        self,
        *,
        tender_id: TenderId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Tender:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        if when < tender.deadline_at:
            raise EntityConflictError("tender cannot close before its authoritative deadline")
        expected_version = tender.version
        tender.close(closed_at=when, correlation_id=correlation_id)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return tender

    def award(
        self,
        *,
        tender_id: TenderId,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> tuple[Tender, Booking]:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        if tender.status is not TenderStatus.CLOSED:
            raise EntityConflictError("tender must be closed before award")

        quote = self._quotes.get(quote_id)
        if quote is None:
            raise EntityNotFoundError("award quote does not exist")
        invitation = self._tenders.find_invitation_for_rfq(quote.rfq_id)
        if invitation is None or invitation.tender_id != tender.id:
            raise EntityConflictError("award quote does not belong to this tender")
        if invitation.status is not TenderInvitationStatus.ACCEPTED:
            raise EntityConflictError("award quote must belong to an accepted invitation")
        if invitation.last_quote_id != quote.id:
            raise EntityConflictError("award quote must be the latest tender revision")
        if (
            tender.best_and_final_requested_at is not None
            and invitation.best_and_final_quote_id != quote.id
        ):
            raise EntityConflictError(
                "award quote must be the supplier's explicit best-and-final submission"
            )
        if quote.status is not QuoteStatus.SUBMITTED or not quote.is_current:
            raise EntityConflictError("award quote must still be current and submitted")

        booking = self._booking_service().accept_quote(
            quote_id=quote.id,
            now=when,
            correlation_id=correlation_id,
            tender_id=tender.id,
        )
        expected_version = tender.version
        tender.award(
            quote_id=quote.id,
            booking_id=booking.id,
            awarded_at=when,
            correlation_id=correlation_id,
        )
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return tender, booking

    def admin_correct(
        self,
        *,
        tender_id: TenderId,
        actor_id: TenderActorId,
        target_type: str,
        target_id: TypedId,
        field_name: str,
        original_value: object,
        replacement_value: object,
        reason: str,
        causation_event_id: EventId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> TenderAdminCorrection:
        when = _utc(now, field_name="now")
        tender = self._get_for_update(tender_id)
        if when < tender.deadline_at:
            raise EntityConflictError(
                "admin correction path is only available at or after the tender deadline"
            )
        correction = TenderAdminCorrection(
            id=TenderAdminCorrectionId.new(),
            tender_id=tender.id,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            field_name=field_name,
            original_value=original_value,
            replacement_value=replacement_value,
            reason=reason,
            corrected_at=when,
            causation_event_id=causation_event_id,
        )
        expected_version = tender.version
        tender.record_admin_correction(correction, correlation_id=correlation_id)
        self._tenders.add_correction(correction)
        self._tenders.save(tender, expected_version=expected_version)
        self._events.add_aggregate_events(tender)
        return correction

    def get(self, tender_id: TenderId) -> Tender:
        tender = self._tenders.get(tender_id)
        if tender is None:
            raise EntityNotFoundError("tender does not exist")
        return tender

    def list_invitations(self, tender_id: TenderId) -> tuple[TenderInvitation, ...]:
        self.get(tender_id)
        return self._tenders.list_invitations(tender_id)

    def supplier_view(
        self,
        *,
        tender_id: TenderId,
        operator_id: OperatorId,
    ) -> TenderSupplierView:
        tender = self.get(tender_id)
        invitation = self._tenders.find_invitation_for_operator(tender.id, operator_id)
        if invitation is None:
            raise EntityNotFoundError("operator is not invited to this tender")
        # Deliberately return only this operator's RFQ lineage. In sealed mode no competitor
        # identifiers, commercial values, revision history, or rank are exposed.
        quotes = self._quotes.list_for_rfq(invitation.rfq_id)
        return TenderSupplierView(tender=tender, invitation=invitation, quotes=quotes)

    def audit_trail(self, tender_id: TenderId) -> TenderAuditTrail:
        tender = self.get(tender_id)
        return TenderAuditTrail(
            tender=tender,
            invitations=self._tenders.list_invitations(tender.id),
            corrections=self._tenders.list_corrections(tender.id),
            events=self._tenders.list_audit_events(tender.id),
        )

    def _rfq_service(self) -> RfqService:
        return RfqService(
            rfqs=self._rfqs,
            missions=self._missions,
            operators=self._operators,
            events=self._events,
            tenders=self._tenders,
        )

    def _quote_service(self) -> QuoteService:
        return QuoteService(
            quotes=self._quotes,
            rfqs=self._rfqs,
            missions=self._missions,
            aircraft=self._aircraft,
            events=self._events,
            tenders=self._tenders,
        )

    def _booking_service(self) -> BookingService:
        return BookingService(
            bookings=self._bookings,
            quotes=self._quotes,
            rfqs=self._rfqs,
            missions=self._missions,
            events=self._events,
            tenders=self._tenders,
        )

    def _get_for_update(self, tender_id: TenderId) -> Tender:
        tender = self._tenders.get_for_update(tender_id)
        if tender is None:
            raise EntityNotFoundError("tender does not exist")
        return tender

    def _get_invitation_for_update(
        self, invitation_id: TenderInvitationId
    ) -> TenderInvitation:
        invitation = self._tenders.get_invitation_for_update(invitation_id)
        if invitation is None:
            raise EntityNotFoundError("tender invitation does not exist")
        return invitation

    def _lock_participation(
        self,
        invitation_id: TenderInvitationId,
        *,
        now: datetime,
    ) -> tuple[TenderInvitation, Tender]:
        invitation = self._get_invitation_for_update(invitation_id)
        if invitation.status is not TenderInvitationStatus.ACCEPTED:
            raise EntityConflictError("supplier must accept the tender invitation before bidding")
        tender = self._get_for_update(invitation.tender_id)
        if tender.status not in (TenderStatus.OPEN, TenderStatus.BEST_AND_FINAL):
            raise EntityConflictError("tender is not accepting supplier bid mutations")
        if now >= tender.deadline_at:
            raise EntityConflictError("tender deadline has passed")
        return invitation, tender

    @staticmethod
    def _assert_latest_quote(invitation: TenderInvitation, quote_id: QuoteId) -> None:
        if invitation.last_quote_id != quote_id:
            raise EntityConflictError("only the invitation's latest tender quote can be mutated")

    @staticmethod
    def _validate_tender_quote_validity(*, tender: Tender, valid_until: datetime) -> None:
        valid = _utc(valid_until, field_name="valid_until")
        if valid <= tender.deadline_at:
            raise EntityConflictError(
                "tender quote valid_until must extend beyond the tender deadline for award"
            )

    @staticmethod
    def _invitation_with_quote(
        invitation: TenderInvitation,
        quote: Quote,
        *,
        best_and_final: bool,
    ) -> TenderInvitation:
        return TenderInvitation(
            id=invitation.id,
            tender_id=invitation.tender_id,
            operator_id=invitation.operator_id,
            rfq_id=invitation.rfq_id,
            status=invitation.status,
            invited_at=invitation.invited_at,
            responded_at=invitation.responded_at,
            last_quote_id=quote.id,
            best_and_final_quote_id=quote.id if best_and_final else invitation.best_and_final_quote_id,
        )
