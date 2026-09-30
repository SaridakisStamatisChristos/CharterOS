from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from charteros.application.capacity import AircraftCapacityPolicy
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.feasibility import AircraftMissionFeasibilityService
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.capacity import AircraftCapacityReservationRepository
from charteros.application.ports.catalog import (
    AircraftRepository,
    DomainEventRepository,
    OperatorRepository,
    OrganizationRepository,
)
from charteros.application.ports.disruptions import DisruptionRepository
from charteros.application.ports.fleet import FleetTimelineRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import Booking, BookingId, BookingState
from charteros.domain.disruptions import (
    Disruption,
    DisruptionBuyerDecision,
    DisruptionBuyerDecisionId,
    DisruptionBuyerDecisionValue,
    DisruptionCommercialChange,
    DisruptionCommercialChangeId,
    DisruptionCommercialChangeStatus,
    DisruptionId,
    DisruptionProposalId,
    DisruptionProposalStatus,
    DisruptionStatus,
    DisruptionType,
    ReplacementProposal,
)
from charteros.domain.missions import Mission
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId, OrganizationStatus
from charteros.domain.quotes import Quote, QuoteStatus
from charteros.domain.quotes.normalization import normalize_quote
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange

MAX_DISRUPTIONS_PER_BOOKING = 100
MAX_PROPOSALS_PER_DISRUPTION = 100


@dataclass(frozen=True, slots=True)
class DisruptionPartyContext:
    buyer_id: OrganizationId | None = None
    operator_id: OperatorId | None = None

    def __post_init__(self) -> None:
        if (self.buyer_id is None) == (self.operator_id is None):
            raise DomainValidationError(
                "exactly one buyer or operator disruption context is required"
            )

    @property
    def actor_id(self) -> TypedId:
        if self.buyer_id is not None:
            return self.buyer_id
        if self.operator_id is None:
            raise DomainValidationError("disruption context has no actor")
        return self.operator_id


@dataclass(frozen=True, slots=True)
class _BookingContext:
    booking: Booking
    mission: Mission
    accepted_quote: Quote


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class DisruptionService:
    """Canonical post-booking disruption evidence without alternate Booking/Quote authority."""

    def __init__(
        self,
        *,
        disruptions: DisruptionRepository,
        bookings: BookingRepository,
        missions: MissionRepository,
        quotes: QuoteRepository,
        rfqs: RfqRepository,
        organizations: OrganizationRepository,
        operators: OperatorRepository,
        aircraft: AircraftRepository,
        fleet_timeline: FleetTimelineRepository,
        feasibility: AircraftMissionFeasibilityService,
        capacity_policy: AircraftCapacityPolicy,
        capacity_reservations: AircraftCapacityReservationRepository,
        events: DomainEventRepository,
    ) -> None:
        self._disruptions = disruptions
        self._bookings = bookings
        self._missions = missions
        self._quotes = quotes
        self._rfqs = rfqs
        self._organizations = organizations
        self._operators = operators
        self._aircraft = aircraft
        self._fleet_timeline = fleet_timeline
        self._feasibility = feasibility
        self._capacity_policy = capacity_policy
        self._capacity_reservations = capacity_reservations
        self._events = events

    def create(
        self,
        *,
        booking_id: BookingId,
        disruption_type: DisruptionType,
        detected_at: datetime,
        effective_at: datetime | None,
        reason: str,
        party: DisruptionPartyContext,
        correlation_id: CorrelationId,
    ) -> Disruption:
        when = _utc(detected_at, field_name="detected_at")
        context = self._booking_context(booking_id, for_update=True)
        self._assert_party(context, party)
        if context.booking.state in (BookingState.COMPLETED, BookingState.RECONCILED):
            raise EntityConflictError(
                "completed or reconciled booking cannot open a new disruption"
            )
        if when < context.booking.created_at:
            raise EntityConflictError("disruption detected_at cannot precede booking creation")
        effective = (
            _utc(effective_at, field_name="effective_at") if effective_at is not None else None
        )
        if effective is not None and effective < context.booking.created_at:
            raise EntityConflictError("disruption effective_at cannot precede booking creation")

        disruption = Disruption.open(
            booking_id=context.booking.id,
            disruption_type=disruption_type,
            detected_at=when,
            effective_at=effective,
            reason=reason,
            actor_id=party.actor_id,
            correlation_id=correlation_id,
        )
        self._disruptions.add(disruption)
        self._events.add_aggregate_events(disruption)
        return disruption

    def get(
        self,
        *,
        disruption_id: DisruptionId,
        party: DisruptionPartyContext,
    ) -> Disruption:
        disruption = self._disruptions.get(disruption_id)
        if disruption is None:
            raise EntityNotFoundError("disruption is not available in this party context")
        context = self._booking_context(disruption.booking_id, for_update=False)
        self._assert_party(context, party)
        return disruption

    def list_for_booking(
        self,
        *,
        booking_id: BookingId,
        party: DisruptionPartyContext,
        limit: int,
    ) -> tuple[Disruption, ...]:
        context = self._booking_context(booking_id, for_update=False)
        self._assert_party(context, party)
        bounded = min(max(limit, 1), MAX_DISRUPTIONS_PER_BOOKING)
        return self._disruptions.list_for_booking(booking_id, limit=bounded)

    def propose_replacement(
        self,
        *,
        disruption_id: DisruptionId,
        operator_id: OperatorId,
        proposed_operator_id: OperatorId | None,
        proposed_aircraft_id: AircraftId | None,
        departure_window: TimeRange | None,
        source: str,
        source_evidence: str | None,
        proposed_at: datetime,
        correlation_id: CorrelationId,
    ) -> ReplacementProposal:
        when = _utc(proposed_at, field_name="proposed_at")
        disruption = self._locked_disruption(disruption_id)
        context = self._booking_context(disruption.booking_id, for_update=True)
        self._assert_operator(context, operator_id)
        self._assert_mutable(disruption)

        target_operator = proposed_operator_id or context.booking.operator_id
        target_aircraft = proposed_aircraft_id or context.booking.aircraft_id
        if target_operator != context.booking.operator_id:
            raise EntityConflictError(
                "cross-operator replacement requires canonical re-procurement; "
                "disruption handling cannot create an alternate award path"
            )

        operator = self._operators.get(target_operator)
        if operator is None:
            raise EntityConflictError("replacement operator does not exist")
        aircraft = self._aircraft.get(target_aircraft)
        if aircraft is None or aircraft.operator_id != target_operator:
            raise EntityConflictError(
                "replacement aircraft is not canonical fleet of the booking operator"
            )

        feasibility = None
        is_operational_change = (
            target_aircraft != context.booking.aircraft_id or departure_window is not None
        )
        if is_operational_change:
            feasibility = self._assert_replacement_feasible(
                context=context,
                aircraft_id=target_aircraft,
                operator_id=target_operator,
                departure_window=departure_window,
                known_as_of=when,
            )

        availability = (
            feasibility.candidate.availability if feasibility is not None else None
        )
        draft = feasibility.evaluation.draft if feasibility is not None else None
        if feasibility is not None and draft is None:
            raise RuntimeError("accepted replacement feasibility is missing its canonical draft")

        current = self._disruptions.get_current_proposal_for_update(disruption.id)
        proposal = ReplacementProposal(
            id=DisruptionProposalId.new(),
            disruption_id=disruption.id,
            revision_number=current.revision_number + 1 if current is not None else 1,
            supersedes_proposal_id=current.id if current is not None else None,
            status=DisruptionProposalStatus.CURRENT,
            proposed_operator_id=target_operator,
            proposed_aircraft_id=target_aircraft,
            proposed_operator_version=operator.version,
            proposed_aircraft_version=aircraft.version,
            availability_record_id=availability.id if availability is not None else None,
            availability_recorded_at=(
                availability.recorded_at if availability is not None else None
            ),
            departure_window=departure_window,
            feasibility_policy_version=(
                feasibility.policy_version if feasibility is not None else None
            ),
            feasibility_known_as_of=(
                feasibility.known_as_of if feasibility is not None else None
            ),
            position_observation_id=(
                draft.position.id if draft is not None else None
            ),
            position_event_time=(
                draft.position.event_time if draft is not None else None
            ),
            position_recorded_at=(
                draft.position.recorded_at if draft is not None else None
            ),
            reference_profile_id=(
                draft.reference_profile.id.value if draft is not None else None
            ),
            reference_profile_recorded_at=(
                draft.reference_profile.recorded_at if draft is not None else None
            ),
            route_distance_tenths_nm=(
                draft.route_distance_tenths_nm if draft is not None else None
            ),
            required_range_nm=(
                draft.required_range_nm if draft is not None else None
            ),
            reposition_distance_tenths_nm=(
                draft.reposition_distance_tenths_nm if draft is not None else None
            ),
            route_minutes=(draft.route_minutes if draft is not None else None),
            reposition_minutes=(
                draft.reposition_minutes if draft is not None else None
            ),
            timing_buffer_minutes=(
                draft.timing_buffer_minutes if draft is not None else None
            ),
            requires_buyer_decision=(
                target_operator != context.booking.operator_id
                or target_aircraft != context.booking.aircraft_id
                or (
                    departure_window is not None
                    and departure_window != context.mission.departure_window
                )
            ),
            source=source,
            source_evidence=source_evidence,
            proposed_at=when,
        )

        expected_version = disruption.version
        if current is not None:
            self._disruptions.supersede_proposal(current.id, superseded_at=when)
            disruption.record_proposal_superseded(
                current.id,
                replacement_proposal_id=proposal.id,
                superseded_at=when,
                actor_id=operator_id,
                correlation_id=correlation_id,
            )
        self._disruptions.add_proposal(proposal)
        disruption.record_proposal(
            proposal,
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        self._disruptions.save(disruption, expected_version=expected_version)
        self._events.add_aggregate_events(disruption)
        return proposal

    def list_proposals(
        self,
        *,
        disruption_id: DisruptionId,
        party: DisruptionPartyContext,
        limit: int,
    ) -> tuple[ReplacementProposal, ...]:
        self.get(disruption_id=disruption_id, party=party)
        bounded = min(max(limit, 1), MAX_PROPOSALS_PER_DISRUPTION)
        return self._disruptions.list_proposals(disruption_id, limit=bounded)

    def create_commercial_change(
        self,
        *,
        disruption_id: DisruptionId,
        proposal_id: DisruptionProposalId,
        operator_id: OperatorId,
        currency: Currency,
        known_adjustment_minor: int,
        conditional_adjustment_minor: int,
        terms_summary: str | None,
        created_at: datetime,
        correlation_id: CorrelationId,
    ) -> DisruptionCommercialChange:
        when = _utc(created_at, field_name="created_at")
        disruption = self._locked_disruption(disruption_id)
        context = self._booking_context(disruption.booking_id, for_update=True)
        self._assert_operator(context, operator_id)
        self._assert_mutable(disruption)

        proposal = self._disruptions.get_current_proposal_for_update(disruption.id)
        if proposal is None or proposal.id != proposal_id:
            raise EntityConflictError("commercial change targets a stale disruption proposal")
        if proposal.proposed_operator_id != context.booking.operator_id:
            raise EntityConflictError("commercial change cannot authorize alternate supplier award")

        quote = context.accepted_quote
        if currency != quote.currency:
            raise EntityConflictError(
                "disruption commercial change must remain in the accepted quote currency; "
                "implicit FX is not available"
            )
        normalization = normalize_quote(quote)
        known = Money(known_adjustment_minor, currency)
        conditional = Money(conditional_adjustment_minor, currency)
        resulting_expected = normalization.expected_total + known
        resulting_worst = normalization.worst_case_total + known + conditional

        current = self._disruptions.get_current_commercial_change_for_update(proposal.id)
        change = DisruptionCommercialChange(
            id=DisruptionCommercialChangeId.new(),
            disruption_id=disruption.id,
            proposal_id=proposal.id,
            revision_number=current.revision_number + 1 if current is not None else 1,
            supersedes_change_id=current.id if current is not None else None,
            status=DisruptionCommercialChangeStatus.CURRENT,
            original_quote_id=quote.id,
            currency=currency,
            normalization_version=normalization.normalization_version,
            original_expected_total=normalization.expected_total,
            original_worst_case_total=normalization.worst_case_total,
            known_adjustment=known,
            conditional_adjustment=conditional,
            resulting_expected_total=resulting_expected,
            resulting_worst_case_total=resulting_worst,
            terms_summary=terms_summary,
            created_at=when,
        )

        expected_version = disruption.version
        if current is not None:
            self._disruptions.supersede_commercial_change(current.id, superseded_at=when)
            disruption.record_commercial_change_superseded(
                current.id,
                replacement_change_id=change.id,
                superseded_at=when,
                actor_id=operator_id,
                correlation_id=correlation_id,
            )
        self._disruptions.add_commercial_change(change)
        disruption.record_commercial_change(
            change,
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        self._disruptions.save(disruption, expected_version=expected_version)
        self._events.add_aggregate_events(disruption)
        return change

    def buyer_decide(
        self,
        *,
        disruption_id: DisruptionId,
        proposal_id: DisruptionProposalId,
        commercial_change_id: DisruptionCommercialChangeId | None,
        buyer_id: OrganizationId,
        decision: DisruptionBuyerDecisionValue,
        decided_at: datetime,
        note: str | None,
        correlation_id: CorrelationId,
    ) -> DisruptionBuyerDecision:
        when = _utc(decided_at, field_name="decided_at")
        disruption = self._locked_disruption(disruption_id)
        context = self._booking_context(disruption.booking_id, for_update=True)
        self._assert_buyer(context, buyer_id)
        self._assert_mutable(disruption)

        proposal = self._disruptions.get_current_proposal_for_update(disruption.id)
        if proposal is None or proposal.id != proposal_id:
            raise EntityConflictError("buyer decision targets a stale disruption proposal")
        current_change = self._disruptions.get_current_commercial_change_for_update(proposal.id)
        actual_change_id = current_change.id if current_change is not None else None
        if commercial_change_id != actual_change_id:
            raise EntityConflictError("buyer decision targets stale disruption commercial evidence")
        if when < proposal.proposed_at:
            raise EntityConflictError("buyer decision cannot precede the current proposal")
        if current_change is not None and when < current_change.created_at:
            raise EntityConflictError("buyer decision cannot precede current commercial evidence")

        buyer_decision = DisruptionBuyerDecision(
            id=DisruptionBuyerDecisionId.new(),
            disruption_id=disruption.id,
            proposal_id=proposal.id,
            commercial_change_id=actual_change_id,
            buyer_id=buyer_id,
            decision=decision,
            decided_at=when,
            note=note,
        )
        expected_version = disruption.version
        self._disruptions.add_buyer_decision(buyer_decision)
        disruption.record_buyer_decision(
            buyer_decision,
            correlation_id=correlation_id,
        )
        self._disruptions.save(disruption, expected_version=expected_version)
        self._events.add_aggregate_events(disruption)
        return buyer_decision

    def resolve(
        self,
        *,
        disruption_id: DisruptionId,
        proposal_id: DisruptionProposalId,
        operator_id: OperatorId,
        resolved_at: datetime,
        outcome: str,
        correlation_id: CorrelationId,
    ) -> Disruption:
        when = _utc(resolved_at, field_name="resolved_at")
        disruption = self._locked_disruption(disruption_id)
        context = self._booking_context(disruption.booking_id, for_update=True)
        self._assert_operator(context, operator_id)
        self._assert_mutable(disruption)

        proposal = self._disruptions.get_current_proposal_for_update(disruption.id)
        if proposal is None or proposal.id != proposal_id:
            raise EntityConflictError("resolution targets a stale disruption proposal")
        if proposal.proposed_operator_id != context.booking.operator_id:
            raise EntityConflictError("resolution cannot create an alternate operator award")
        if (
            proposal.proposed_aircraft_id != context.booking.aircraft_id
            or proposal.departure_window is not None
        ):
            self._assert_replacement_feasible(
                context=context,
                aircraft_id=proposal.proposed_aircraft_id,
                operator_id=proposal.proposed_operator_id,
                departure_window=proposal.departure_window,
                known_as_of=when,
            )
        commercial = self._disruptions.get_current_commercial_change_for_update(proposal.id)

        buyer_required = proposal.requires_buyer_decision or commercial is not None
        buyer_decision = None
        if disruption.latest_buyer_decision_id is not None:
            buyer_decision = self._disruptions.get_buyer_decision(
                disruption.latest_buyer_decision_id
            )
        if buyer_required and (
            buyer_decision is None
            or buyer_decision.proposal_id != proposal.id
            or buyer_decision.commercial_change_id
            != (commercial.id if commercial is not None else None)
            or buyer_decision.decision is not DisruptionBuyerDecisionValue.APPROVED
            or disruption.status is not DisruptionStatus.BUYER_APPROVED
        ):
            raise EntityConflictError(
                "current disruption proposal/commercial evidence requires explicit buyer approval"
            )

        expected_version = disruption.version
        disruption.resolve(
            proposal=proposal,
            commercial_change=commercial,
            buyer_decision=buyer_decision,
            booking_state=context.booking.state,
            resolved_at=when,
            outcome=outcome,
            actor_id=operator_id,
            correlation_id=correlation_id,
        )
        self._disruptions.save(disruption, expected_version=expected_version)
        self._events.add_aggregate_events(disruption)
        return disruption

    def _assert_replacement_feasible(
        self,
        *,
        context: _BookingContext,
        aircraft_id: AircraftId,
        operator_id: OperatorId,
        departure_window: TimeRange | None,
        known_as_of: datetime,
    ):
        result = self._feasibility.evaluate(
            mission=context.mission,
            aircraft_id=aircraft_id,
            known_as_of=known_as_of,
            departure_window=departure_window,
        )
        if result.candidate.operator_id != operator_id:
            raise EntityConflictError(
                "replacement aircraft/operator snapshot lineage is inconsistent"
            )
        if not result.feasible:
            reasons = ",".join(reason.value for reason in result.evaluation.rejection_reasons)
            raise EntityConflictError(
                f"replacement aircraft is not canonically feasible for the mission ({reasons})"
            )
        draft = result.evaluation.draft
        if draft is None:
            raise RuntimeError("feasible replacement is missing its canonical matching draft")

        capacity_plan = self._capacity_policy.derive(
            mission=context.mission,
            aircraft_id=aircraft_id,
            operator_id=operator_id,
            known_as_of=known_as_of,
            departure_window=result.departure_window,
        )
        if capacity_plan.reference_profile_id != draft.reference_profile.id.value:
            raise EntityConflictError(
                "replacement capacity and feasibility reference evidence disagree"
            )
        if self._capacity_reservations.has_reserved_overlap(
            aircraft_id=aircraft_id,
            interval=capacity_plan.interval,
            exclude_booking_id=context.booking.id,
        ):
            raise EntityConflictError(
                "replacement aircraft has overlapping committed CharterOS capacity"
            )
        return result

    def _locked_disruption(self, disruption_id: DisruptionId) -> Disruption:
        disruption = self._disruptions.get_for_update(disruption_id)
        if disruption is None:
            raise EntityNotFoundError("disruption does not exist")
        return disruption

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
        party: DisruptionPartyContext,
    ) -> None:
        if party.buyer_id is not None:
            self._assert_buyer(context, party.buyer_id)
            return
        if party.operator_id is None:
            raise DomainValidationError("disruption context has no actor")
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
            raise EntityNotFoundError("disruption is not available in the buyer context")

    def _assert_operator(
        self,
        context: _BookingContext,
        operator_id: OperatorId,
    ) -> None:
        operator = self._operators.get(operator_id)
        if operator is None or context.booking.operator_id != operator_id:
            raise EntityNotFoundError("disruption is not available in the operator context")

    @staticmethod
    def _assert_mutable(disruption: Disruption) -> None:
        if disruption.status is DisruptionStatus.RESOLVED:
            raise EntityConflictError("resolved disruption is terminal")
