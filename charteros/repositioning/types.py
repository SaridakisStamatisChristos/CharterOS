from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from charteros.application.graph_queries import EmptyLegCandidate
from charteros.domain.aircraft import AircraftId
from charteros.domain.airports import AirportId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.normalization import PricingConfidence
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange

POLICY_VERSION = "reposition-v1"


class RepositionReasonCode(StrEnum):
    FEASIBLE = "feasible"
    AIRCRAFT_INACTIVE = "aircraft_inactive"
    OPERATOR_UNVERIFIED = "operator_unverified"
    OPERATOR_INSURANCE_INVALID = "operator_insurance_invalid"
    OPERATOR_COMMERCIAL_INACTIVE = "operator_commercial_inactive"
    NO_REFERENCE_PROFILE = "no_reference_profile"
    BASELINE_REPOSITION_TOO_FAR = "baseline_reposition_too_far"
    BASELINE_REPOSITION_RANGE = "baseline_reposition_range"
    BASELINE_REPOSITION_TOO_LATE = "baseline_reposition_too_late"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"
    INSUFFICIENT_ROUTE_RANGE = "insufficient_route_range"
    PRE_REPOSITION_TOO_FAR = "pre_reposition_too_far"
    PRE_REPOSITION_RANGE = "pre_reposition_range"
    POST_REPOSITION_TOO_FAR = "post_reposition_too_far"
    POST_REPOSITION_RANGE = "post_reposition_range"
    MISSION_WINDOW_INFEASIBLE = "mission_window_infeasible"
    CURRENCY_MISMATCH = "currency_mismatch"
    NON_POSITIVE_MARGIN = "non_positive_margin"


@dataclass(frozen=True, slots=True)
class QuotedFutureLeg:
    mission_id: MissionId
    rfq_id: RfqId
    quote_id: QuoteId
    aircraft_id: AircraftId
    operator_id: OperatorId
    origin_airport_id: AirportId
    destination_airport_id: AirportId
    departure_window: TimeRange
    passenger_count: int
    revenue: Money
    worst_case_revenue: Money
    totals_complete: bool
    pricing_confidence: PricingConfidence


@dataclass(frozen=True, slots=True)
class BaselineEmptyLeg:
    structural: EmptyLegCandidate
    baseline_distance_tenths_nm: int
    baseline_minutes: int
    baseline_reposition_cost: Money


@dataclass(frozen=True, slots=True)
class FeasibleInsertion:
    empty_leg: BaselineEmptyLeg
    opportunity: QuotedFutureLeg
    scheduled_departure: datetime
    continuity_ready_at: datetime
    pre_reposition_distance_tenths_nm: int
    revenue_distance_tenths_nm: int
    post_reposition_distance_tenths_nm: int
    pre_reposition_minutes: int
    revenue_minutes: int
    post_reposition_minutes: int
    reposition_cost: Money
    revenue_leg_operating_cost: Money
    revenue: Money
    gross_margin: Money
    opportunity_cost: Money
    margin: Money

    @property
    def currency(self) -> Currency:
        return self.margin.currency


@dataclass(frozen=True, slots=True)
class RepositionAssignment:
    aircraft_id: AircraftId
    operator_id: OperatorId
    previous_booking_id: str
    next_booking_id: str
    mission_id: MissionId
    quote_id: QuoteId
    from_airport_id: AirportId
    mission_origin_airport_id: AirportId
    mission_destination_airport_id: AirportId
    continuity_airport_id: AirportId
    window_start: datetime
    window_end: datetime
    scheduled_departure: datetime
    continuity_ready_at: datetime
    baseline_reposition_distance_tenths_nm: int
    pre_reposition_distance_tenths_nm: int
    revenue_distance_tenths_nm: int
    post_reposition_distance_tenths_nm: int
    baseline_reposition_cost: Money
    reposition_cost: Money
    revenue_leg_operating_cost: Money
    revenue: Money
    gross_margin: Money
    opportunity_cost: Money
    margin: Money
    pricing_confidence: PricingConfidence
    totals_complete: bool


@dataclass(frozen=True, slots=True)
class CurrencyOptimizationPlan:
    currency: Currency
    candidate_count: int
    assignment_count: int
    total_margin: Money
    assignments: tuple[RepositionAssignment, ...]


@dataclass(frozen=True, slots=True)
class RepositionOptimization:
    policy_version: str
    projection_version: int
    evaluated_at: datetime
    window_start: datetime
    window_end: datetime
    structural_empty_leg_count: int
    feasible_empty_leg_count: int
    quoted_future_leg_count: int
    feasible_candidate_count: int
    rejection_summary: dict[RepositionReasonCode, int]
    global_plan_available: bool
    currency_plans: tuple[CurrencyOptimizationPlan, ...]
