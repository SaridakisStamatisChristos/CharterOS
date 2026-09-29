from datetime import UTC, datetime, timedelta

import pytest

from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import BookingId, BookingState
from charteros.domain.disruptions import (
    Disruption,
    DisruptionBuyerDecision,
    DisruptionBuyerDecisionId,
    DisruptionBuyerDecisionValue,
    DisruptionCommercialChange,
    DisruptionCommercialChangeId,
    DisruptionCommercialChangeStatus,
    DisruptionProposalId,
    DisruptionProposalStatus,
    DisruptionStatus,
    DisruptionType,
    ReplacementProposal,
)
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange


@pytest.mark.parametrize("disruption_type", list(DisruptionType))
def test_all_disruption_types_open_as_typed_canonical_evidence(
    disruption_type: DisruptionType,
) -> None:
    now = datetime.now(UTC)
    disruption = Disruption.open(
        booking_id=BookingId.new(),
        disruption_type=disruption_type,
        detected_at=now,
        effective_at=now + timedelta(minutes=15),
        reason=f"Operational {disruption_type.value}",
        actor_id=OperatorId.new(),
        correlation_id=CorrelationId.new(),
    )

    assert disruption.disruption_type is disruption_type
    assert disruption.status is DisruptionStatus.OPEN
    assert disruption.version == 1
    assert [event.event_type for event in disruption.pending_events] == ["DISRUPTION_OPENED"]


def _proposal(
    disruption: Disruption,
    *,
    now: datetime,
    requires_buyer: bool = True,
    revision: int = 1,
    supersedes: DisruptionProposalId | None = None,
) -> ReplacementProposal:
    return ReplacementProposal(
        id=DisruptionProposalId.new(),
        disruption_id=disruption.id,
        revision_number=revision,
        supersedes_proposal_id=supersedes,
        status=DisruptionProposalStatus.CURRENT,
        proposed_operator_id=OperatorId.new(),
        proposed_aircraft_id=AircraftId.new(),
        departure_window=TimeRange(now + timedelta(hours=2), now + timedelta(hours=3)),
        requires_buyer_decision=requires_buyer,
        source="operations",
        source_evidence="dispatch review",
        proposed_at=now + timedelta(minutes=1),
    )


def _commercial(
    disruption: Disruption,
    proposal: ReplacementProposal,
    *,
    now: datetime,
) -> DisruptionCommercialChange:
    currency = Currency("EUR")
    return DisruptionCommercialChange(
        id=DisruptionCommercialChangeId.new(),
        disruption_id=disruption.id,
        proposal_id=proposal.id,
        revision_number=1,
        supersedes_change_id=None,
        status=DisruptionCommercialChangeStatus.CURRENT,
        original_quote_id=QuoteId.new(),
        currency=currency,
        normalization_version="v1",
        original_expected_total=Money(10_000_000, currency),
        original_worst_case_total=Money(10_500_000, currency),
        known_adjustment=Money(100_000, currency),
        conditional_adjustment=Money(50_000, currency),
        resulting_expected_total=Money(10_100_000, currency),
        resulting_worst_case_total=Money(10_650_000, currency),
        terms_summary="Additional handling and conditional deicing",
        created_at=now + timedelta(minutes=2),
    )


def test_material_resolution_requires_decision_bound_to_exact_current_evidence() -> None:
    now = datetime.now(UTC)
    buyer_id = OrganizationId.new()
    operator_id = OperatorId.new()
    disruption = Disruption.open(
        booking_id=BookingId.new(),
        disruption_type=DisruptionType.TECHNICAL,
        detected_at=now,
        effective_at=None,
        reason="Aircraft technical issue",
        actor_id=operator_id,
        correlation_id=CorrelationId.new(),
    )
    proposal = _proposal(disruption, now=now)
    disruption.record_proposal(
        proposal,
        actor_id=operator_id,
        correlation_id=CorrelationId.new(),
    )
    change = _commercial(disruption, proposal, now=now)
    disruption.record_commercial_change(
        change,
        actor_id=operator_id,
        correlation_id=CorrelationId.new(),
    )

    stale_decision = DisruptionBuyerDecision(
        id=DisruptionBuyerDecisionId.new(),
        disruption_id=disruption.id,
        proposal_id=proposal.id,
        commercial_change_id=None,
        buyer_id=buyer_id,
        decision=DisruptionBuyerDecisionValue.APPROVED,
        decided_at=now + timedelta(minutes=3),
    )
    with pytest.raises(DomainValidationError):
        disruption.record_buyer_decision(
            stale_decision,
            correlation_id=CorrelationId.new(),
        )

    decision = DisruptionBuyerDecision(
        id=DisruptionBuyerDecisionId.new(),
        disruption_id=disruption.id,
        proposal_id=proposal.id,
        commercial_change_id=change.id,
        buyer_id=buyer_id,
        decision=DisruptionBuyerDecisionValue.APPROVED,
        decided_at=now + timedelta(minutes=3),
    )
    disruption.record_buyer_decision(decision, correlation_id=CorrelationId.new())
    disruption.resolve(
        proposal=proposal,
        commercial_change=change,
        buyer_decision=decision,
        booking_state=BookingState.PRE_OPERATION,
        resolved_at=now + timedelta(minutes=4),
        outcome="Replacement aircraft accepted and dispatched",
        actor_id=operator_id,
        correlation_id=CorrelationId.new(),
    )

    assert disruption.status is DisruptionStatus.RESOLVED
    assert disruption.selected_proposal_id == proposal.id
    assert disruption.selected_commercial_change_id == change.id
    assert disruption.selected_buyer_decision_id == decision.id
    assert disruption.booking_state_at_resolution is BookingState.PRE_OPERATION
    assert disruption.pending_events[-1].event_type == "DISRUPTION_RESOLVED"

    with pytest.raises(DomainValidationError):
        disruption.record_proposal(
            _proposal(disruption, now=now + timedelta(minutes=10)),
            actor_id=operator_id,
            correlation_id=CorrelationId.new(),
        )


def test_buyer_rejection_cannot_resolve_material_proposal() -> None:
    now = datetime.now(UTC)
    buyer_id = OrganizationId.new()
    operator_id = OperatorId.new()
    disruption = Disruption.open(
        booking_id=BookingId.new(),
        disruption_type=DisruptionType.DELAY,
        detected_at=now,
        effective_at=None,
        reason="Crew positioning delay",
        actor_id=operator_id,
    )
    proposal = _proposal(disruption, now=now, requires_buyer=True)
    disruption.record_proposal(proposal, actor_id=operator_id)

    decision = DisruptionBuyerDecision(
        id=DisruptionBuyerDecisionId.new(),
        disruption_id=disruption.id,
        proposal_id=proposal.id,
        commercial_change_id=None,
        buyer_id=buyer_id,
        decision=DisruptionBuyerDecisionValue.REJECTED,
        decided_at=now + timedelta(minutes=2),
    )
    disruption.record_buyer_decision(decision)
    assert disruption.status is DisruptionStatus.BUYER_REJECTED

    with pytest.raises(DomainValidationError):
        disruption.resolve(
            proposal=proposal,
            commercial_change=None,
            buyer_decision=decision,
            booking_state=BookingState.CONFIRMED,
            resolved_at=now + timedelta(minutes=3),
            outcome="Should not resolve",
            actor_id=operator_id,
        )


def test_commercial_change_is_exact_money_and_rejects_inconsistent_or_negative_totals() -> None:
    now = datetime.now(UTC)
    disruption = Disruption.open(
        booking_id=BookingId.new(),
        disruption_type=DisruptionType.WEATHER,
        detected_at=now,
        effective_at=None,
        reason="Weather reroute",
        actor_id=OperatorId.new(),
    )
    proposal = _proposal(disruption, now=now)
    eur = Currency("EUR")
    usd = Currency("USD")

    with pytest.raises(DomainValidationError):
        DisruptionCommercialChange(
            id=DisruptionCommercialChangeId.new(),
            disruption_id=disruption.id,
            proposal_id=proposal.id,
            revision_number=1,
            supersedes_change_id=None,
            status=DisruptionCommercialChangeStatus.CURRENT,
            original_quote_id=QuoteId.new(),
            currency=eur,
            normalization_version="v1",
            original_expected_total=Money(1_000, eur),
            original_worst_case_total=Money(1_200, eur),
            known_adjustment=Money(100, usd),
            conditional_adjustment=Money(0, eur),
            resulting_expected_total=Money(1_100, eur),
            resulting_worst_case_total=Money(1_300, eur),
            terms_summary="Mismatched currency must fail",
            created_at=now,
        )

    with pytest.raises(DomainValidationError):
        DisruptionCommercialChange(
            id=DisruptionCommercialChangeId.new(),
            disruption_id=disruption.id,
            proposal_id=proposal.id,
            revision_number=1,
            supersedes_change_id=None,
            status=DisruptionCommercialChangeStatus.CURRENT,
            original_quote_id=QuoteId.new(),
            currency=eur,
            normalization_version="v1",
            original_expected_total=Money(1_000, eur),
            original_worst_case_total=Money(1_200, eur),
            known_adjustment=Money(-2_000, eur),
            conditional_adjustment=Money(0, eur),
            resulting_expected_total=Money(-1_000, eur),
            resulting_worst_case_total=Money(-800, eur),
            terms_summary="Impossible negative result",
            created_at=now,
        )
