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
        if mission.status not in (MissionStatus.OPEN, MissionStatus.SOURCING):
            raise EntityConflictError("tender can only be created for an open or sourcing mission")
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
