from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from charteros.domain.airports import Airport
from charteros.domain.operators import CommercialStatus, InsuranceStatus, VerificationStatus
from charteros.domain.shared.money import Money
from charteros.matching import (
    MatchingCandidateSnapshot,
    flight_minutes,
    haversine_distance_tenths_nm,
    operating_cost_for_minutes,
    required_range_nm,
)
from charteros.repositioning.types import (
    BaselineEmptyLeg,
    FeasibleInsertion,
    QuotedFutureLeg,
    RepositionReasonCode,
    StructuralEmptyLeg,
)


@dataclass(frozen=True, slots=True)
class BaselineEvaluation:
    baseline: BaselineEmptyLeg | None
    reasons: tuple[RepositionReasonCode, ...]


@dataclass(frozen=True, slots=True)
class InsertionEvaluation:
    insertion: FeasibleInsertion | None
    reasons: tuple[RepositionReasonCode, ...]


def _aircraft_reasons(candidate: MatchingCandidateSnapshot) -> tuple[RepositionReasonCode, ...]:
    reasons: list[RepositionReasonCode] = []
    if candidate.aircraft_status.value != "active":
        reasons.append(RepositionReasonCode.AIRCRAFT_INACTIVE)
    if candidate.verification_status is not VerificationStatus.VERIFIED:
        reasons.append(RepositionReasonCode.OPERATOR_UNVERIFIED)
    if candidate.insurance_status is not InsuranceStatus.VALID:
        reasons.append(RepositionReasonCode.OPERATOR_INSURANCE_INVALID)
    if candidate.commercial_status is not CommercialStatus.ACTIVE:
        reasons.append(RepositionReasonCode.OPERATOR_COMMERCIAL_INACTIVE)
    if candidate.reference_profile is None:
        reasons.append(RepositionReasonCode.NO_REFERENCE_PROFILE)
    return tuple(reasons)


def evaluate_baseline(
    *,
    structural: StructuralEmptyLeg,
    candidate: MatchingCandidateSnapshot,
    previous_origin_airport: Airport,
    from_airport: Airport,
    continuity_airport: Airport,
) -> BaselineEvaluation:
    reasons = _aircraft_reasons(candidate)
    if reasons:
        return BaselineEvaluation(baseline=None, reasons=reasons)

    profile = candidate.reference_profile
    assert profile is not None
    previous_revenue_distance = haversine_distance_tenths_nm(
        previous_origin_airport.latitude,
        previous_origin_airport.longitude,
        from_airport.latitude,
        from_airport.longitude,
    )
    if required_range_nm(previous_revenue_distance) > candidate.range_nm:
        return BaselineEvaluation(
            baseline=None,
            reasons=(RepositionReasonCode.INSUFFICIENT_ROUTE_RANGE,),
        )
    previous_revenue_minutes = flight_minutes(
        previous_revenue_distance,
        profile.cruise_speed_kts,
    )
    aircraft_available_at = structural.window_start + timedelta(
        minutes=previous_revenue_minutes + profile.turnaround_buffer_minutes
    )

    distance = haversine_distance_tenths_nm(
        from_airport.latitude,
        from_airport.longitude,
        continuity_airport.latitude,
        continuity_airport.longitude,
    )
    minutes = flight_minutes(distance, profile.cruise_speed_kts)
    ready_at = aircraft_available_at + timedelta(
        minutes=minutes + profile.turnaround_buffer_minutes
    )

    baseline_reasons: list[RepositionReasonCode] = []
    if distance > profile.max_reposition_nm * 10:
        baseline_reasons.append(RepositionReasonCode.BASELINE_REPOSITION_TOO_FAR)
    if required_range_nm(distance) > candidate.range_nm:
        baseline_reasons.append(RepositionReasonCode.BASELINE_REPOSITION_RANGE)
    if ready_at > structural.window_end:
        baseline_reasons.append(RepositionReasonCode.BASELINE_REPOSITION_TOO_LATE)

    return BaselineEvaluation(
        baseline=BaselineEmptyLeg(
            structural=structural,
            aircraft_available_at=aircraft_available_at,
            previous_revenue_distance_tenths_nm=previous_revenue_distance,
            previous_revenue_minutes=previous_revenue_minutes,
            baseline_distance_tenths_nm=distance,
            baseline_minutes=minutes,
            baseline_reposition_cost=operating_cost_for_minutes(
                profile.operating_cost_per_hour,
                minutes,
            ),
            baseline_reposition_feasible=not baseline_reasons,
        ),
        reasons=tuple(baseline_reasons),
    )


def evaluate_insertion(
    *,
    baseline: BaselineEmptyLeg,
    candidate: MatchingCandidateSnapshot,
    opportunity: QuotedFutureLeg,
    from_airport: Airport,
    mission_origin: Airport,
    mission_destination: Airport,
    continuity_airport: Airport,
) -> InsertionEvaluation:
    profile = candidate.reference_profile
    assert profile is not None

    if opportunity.passenger_count > candidate.seat_capacity:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.INSUFFICIENT_CAPACITY,),
        )
    if opportunity.revenue.currency != profile.operating_cost_per_hour.currency:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.CURRENCY_MISMATCH,),
        )

    pre_distance = haversine_distance_tenths_nm(
        from_airport.latitude,
        from_airport.longitude,
        mission_origin.latitude,
        mission_origin.longitude,
    )
    route_distance = haversine_distance_tenths_nm(
        mission_origin.latitude,
        mission_origin.longitude,
        mission_destination.latitude,
        mission_destination.longitude,
    )
    post_distance = haversine_distance_tenths_nm(
        mission_destination.latitude,
        mission_destination.longitude,
        continuity_airport.latitude,
        continuity_airport.longitude,
    )

    if pre_distance > profile.max_reposition_nm * 10:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.PRE_REPOSITION_TOO_FAR,),
        )
    if required_range_nm(pre_distance) > candidate.range_nm:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.PRE_REPOSITION_RANGE,),
        )
    if required_range_nm(route_distance) > candidate.range_nm:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.INSUFFICIENT_ROUTE_RANGE,),
        )
    if post_distance > profile.max_reposition_nm * 10:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.POST_REPOSITION_TOO_FAR,),
        )
    if required_range_nm(post_distance) > candidate.range_nm:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.POST_REPOSITION_RANGE,),
        )

    pre_minutes = flight_minutes(pre_distance, profile.cruise_speed_kts)
    route_minutes = flight_minutes(route_distance, profile.cruise_speed_kts)
    post_minutes = flight_minutes(post_distance, profile.cruise_speed_kts)
    buffer = profile.turnaround_buffer_minutes

    earliest_after_preposition = baseline.aircraft_available_at + timedelta(
        minutes=pre_minutes + buffer
    )
    scheduled_departure = max(
        opportunity.departure_window.start,
        earliest_after_preposition,
    )
    if scheduled_departure >= opportunity.departure_window.end:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.MISSION_WINDOW_INFEASIBLE,),
        )

    continuity_ready_at = scheduled_departure + timedelta(
        minutes=route_minutes + buffer + post_minutes + buffer
    )
    if continuity_ready_at > baseline.structural.window_end:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.MISSION_WINDOW_INFEASIBLE,),
        )

    reposition_cost = operating_cost_for_minutes(
        profile.operating_cost_per_hour,
        pre_minutes + post_minutes,
    )
    revenue_leg_cost = operating_cost_for_minutes(
        profile.operating_cost_per_hour,
        route_minutes,
    )
    gross_margin = opportunity.revenue - reposition_cost - revenue_leg_cost
    extra_reposition_minor = (
        max(
            0,
            reposition_cost.amount_minor - baseline.baseline_reposition_cost.amount_minor,
        )
        if baseline.baseline_reposition_feasible
        else reposition_cost.amount_minor
    )
    opportunity_cost = Money(extra_reposition_minor, opportunity.revenue.currency)
    margin = opportunity.revenue - revenue_leg_cost - opportunity_cost
    if margin.amount_minor <= 0:
        return InsertionEvaluation(
            insertion=None,
            reasons=(RepositionReasonCode.NON_POSITIVE_MARGIN,),
        )

    return InsertionEvaluation(
        insertion=FeasibleInsertion(
            empty_leg=baseline,
            opportunity=opportunity,
            scheduled_departure=scheduled_departure,
            continuity_ready_at=continuity_ready_at,
            pre_reposition_distance_tenths_nm=pre_distance,
            revenue_distance_tenths_nm=route_distance,
            post_reposition_distance_tenths_nm=post_distance,
            pre_reposition_minutes=pre_minutes,
            revenue_minutes=route_minutes,
            post_reposition_minutes=post_minutes,
            reposition_cost=reposition_cost,
            revenue_leg_operating_cost=revenue_leg_cost,
            revenue=opportunity.revenue,
            gross_margin=gross_margin,
            opportunity_cost=opportunity_cost,
            margin=margin,
        ),
        reasons=(RepositionReasonCode.FEASIBLE,),
    )


def merge_rejection_counts(
    evaluations: tuple[BaselineEvaluation | InsertionEvaluation, ...],
) -> dict[RepositionReasonCode, int]:
    counter: Counter[RepositionReasonCode] = Counter()
    for evaluation in evaluations:
        counter.update(
            reason for reason in evaluation.reasons if reason is not RepositionReasonCode.FEASIBLE
        )
    return dict(sorted(counter.items(), key=lambda item: item[0].value))


def distance_nm(tenths_nm: int) -> Decimal:
    return Decimal(tenths_nm) / Decimal(10)
