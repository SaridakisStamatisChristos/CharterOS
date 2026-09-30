from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from math import asin, cos, radians, sin, sqrt

from charteros.domain.aircraft import AircraftStatus, AvailabilityStatus
from charteros.domain.missions import Mission
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    VerificationStatus,
)
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money
from charteros.matching.types import (
    MAX_SCORE_BASIS_POINTS,
    RANGE_RESERVE_PERCENT,
    REFERENCE_CURRENCY,
    BudgetComparison,
    CandidateEvaluation,
    MatchDraft,
    MatchingCandidateSnapshot,
    MatchReasonCode,
    ensure_utc,
)

_EARTH_RADIUS_NM = 3440.065


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise DomainValidationError("denominator must be positive")
    if numerator < 0:
        raise DomainValidationError("numerator cannot be negative")
    return (numerator + denominator - 1) // denominator


def haversine_distance_tenths_nm(
    latitude_a: Decimal,
    longitude_a: Decimal,
    latitude_b: Decimal,
    longitude_b: Decimal,
) -> int:
    """Return great-circle distance rounded half-up to 0.1 nautical mile."""
    lat1 = radians(float(latitude_a))
    lon1 = radians(float(longitude_a))
    lat2 = radians(float(latitude_b))
    lon2 = radians(float(longitude_b))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    distance_nm = 2 * _EARTH_RADIUS_NM * asin(min(1.0, sqrt(a)))
    rounded = Decimal(str(distance_nm * 10)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded)


def required_range_nm(route_distance_tenths_nm: int) -> int:
    if route_distance_tenths_nm < 0:
        raise DomainValidationError("route distance cannot be negative")
    required_tenths = _ceil_div(route_distance_tenths_nm * (100 + RANGE_RESERVE_PERCENT), 100)
    return _ceil_div(required_tenths, 10)


def flight_minutes(distance_tenths_nm: int, cruise_speed_kts: int) -> int:
    if distance_tenths_nm < 0:
        raise DomainValidationError("distance cannot be negative")
    if cruise_speed_kts <= 0:
        raise DomainValidationError("cruise_speed_kts must be positive")
    return _ceil_div(distance_tenths_nm * 6, cruise_speed_kts)


def operating_cost_for_minutes(cost_per_hour: Money, minutes: int) -> Money:
    if minutes < 0:
        raise DomainValidationError("minutes cannot be negative")
    return Money(
        _ceil_div(cost_per_hour.amount_minor * minutes, 60),
        cost_per_hour.currency,
    )


def _budget_comparison(
    mission: Mission,
    estimated_cost: Money,
) -> tuple[BudgetComparison, int | None, MatchReasonCode]:
    budget = mission.max_budget
    if budget is None:
        return BudgetComparison.NOT_PROVIDED, None, MatchReasonCode.BUDGET_NOT_PROVIDED
    if budget.currency != estimated_cost.currency:
        return (
            BudgetComparison.CURRENCY_MISMATCH,
            None,
            MatchReasonCode.BUDGET_CURRENCY_MISMATCH,
        )
    delta = budget.amount_minor - estimated_cost.amount_minor
    if delta >= 0:
        return BudgetComparison.WITHIN, delta, MatchReasonCode.BUDGET_WITHIN
    return BudgetComparison.EXCEEDED, delta, MatchReasonCode.BUDGET_EXCEEDED


def evaluate_candidate(
    *,
    mission: Mission,
    candidate: MatchingCandidateSnapshot,
    origin_latitude: Decimal,
    origin_longitude: Decimal,
    route_distance_tenths_nm: int,
    decision_time: datetime,
) -> CandidateEvaluation:
    rejection: list[MatchReasonCode] = []

    if candidate.aircraft_status is not AircraftStatus.ACTIVE:
        rejection.append(MatchReasonCode.AIRCRAFT_INACTIVE)
    if candidate.verification_status is not VerificationStatus.VERIFIED:
        rejection.append(MatchReasonCode.OPERATOR_UNVERIFIED)
    if candidate.insurance_status is not InsuranceStatus.VALID:
        rejection.append(MatchReasonCode.OPERATOR_INSURANCE_INVALID)
    if candidate.commercial_status is not CommercialStatus.ACTIVE:
        rejection.append(MatchReasonCode.OPERATOR_COMMERCIAL_INACTIVE)
    if candidate.seat_capacity < mission.passenger_count:
        rejection.append(MatchReasonCode.INSUFFICIENT_CAPACITY)

    required_range = required_range_nm(route_distance_tenths_nm)
    if candidate.range_nm < required_range:
        rejection.append(MatchReasonCode.INSUFFICIENT_RANGE)

    availability = candidate.availability
    if availability is None:
        rejection.append(MatchReasonCode.NO_AVAILABILITY)
    elif availability.status is not AvailabilityStatus.AVAILABLE:
        rejection.append(MatchReasonCode.NOT_AVAILABLE)

    position = candidate.position
    if position is None:
        rejection.append(MatchReasonCode.NO_POSITION)

    profile = candidate.reference_profile
    if profile is None:
        rejection.append(MatchReasonCode.NO_REFERENCE_PROFILE)
    elif profile.operating_cost_per_hour.currency != REFERENCE_CURRENCY:
        rejection.append(MatchReasonCode.REFERENCE_CURRENCY_UNSUPPORTED)

    if rejection:
        return CandidateEvaluation(draft=None, rejection_reasons=tuple(rejection))

    if availability is None or position is None or profile is None:
        raise DomainValidationError("accepted matching candidate is missing required decision inputs")

    reposition_distance = haversine_distance_tenths_nm(
        position.latitude,
        position.longitude,
        origin_latitude,
        origin_longitude,
    )
    if reposition_distance > profile.max_reposition_nm * 10:
        return CandidateEvaluation(
            draft=None,
            rejection_reasons=(MatchReasonCode.REPOSITION_TOO_FAR,),
        )

    decision_utc = ensure_utc(decision_time, field_name="decision_time")
    available_seconds = (mission.departure_window.end - decision_utc).total_seconds()
    available_minutes = max(0, int(available_seconds // 60))
    reposition_minutes = flight_minutes(reposition_distance, profile.cruise_speed_kts)
    required_reposition_minutes = reposition_minutes + profile.turnaround_buffer_minutes
    if required_reposition_minutes > available_minutes:
        return CandidateEvaluation(
            draft=None,
            rejection_reasons=(MatchReasonCode.REPOSITION_TOO_LATE,),
        )

    timing_buffer_minutes = available_minutes - required_reposition_minutes
    route_minutes = flight_minutes(route_distance_tenths_nm, profile.cruise_speed_kts)
    estimated_cost = operating_cost_for_minutes(
        profile.operating_cost_per_hour,
        route_minutes + reposition_minutes,
    )
    schedule_risk = min(
        MAX_SCORE_BASIS_POINTS,
        _ceil_div(
            required_reposition_minutes * MAX_SCORE_BASIS_POINTS,
            max(1, available_minutes),
        ),
    )
    budget_comparison, budget_delta, budget_reason = _budget_comparison(mission, estimated_cost)
    reasons = (
        MatchReasonCode.FEASIBLE,
        MatchReasonCode.AIRCRAFT_ACTIVE,
        MatchReasonCode.OPERATOR_VERIFIED,
        MatchReasonCode.OPERATOR_INSURANCE_VALID,
        MatchReasonCode.OPERATOR_COMMERCIAL_ACTIVE,
        MatchReasonCode.CAPACITY_OK,
        MatchReasonCode.RANGE_OK,
        MatchReasonCode.AVAILABILITY_OK,
        MatchReasonCode.POSITION_KNOWN,
        MatchReasonCode.REFERENCE_PROFILE_OK,
        MatchReasonCode.REFERENCE_CURRENCY_OK,
        MatchReasonCode.REPOSITION_DISTANCE_OK,
        MatchReasonCode.REPOSITION_TIMING_OK,
        budget_reason,
    )
    return CandidateEvaluation(
        draft=MatchDraft(
            aircraft_id=candidate.aircraft_id,
            operator_id=candidate.operator_id,
            aircraft_type_id=candidate.aircraft_type_id,
            position=position,
            availability=availability,
            reference_profile=profile,
            route_distance_tenths_nm=route_distance_tenths_nm,
            required_range_nm=required_range,
            reposition_distance_tenths_nm=reposition_distance,
            route_minutes=route_minutes,
            reposition_minutes=reposition_minutes,
            timing_buffer_minutes=timing_buffer_minutes,
            schedule_risk_basis_points=schedule_risk,
            estimated_operating_cost=estimated_cost,
            budget_comparison=budget_comparison,
            budget_delta_minor=budget_delta,
            reason_codes=reasons,
        ),
        rejection_reasons=(),
    )
