from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from charteros.domain.aircraft import AircraftId, AircraftStatus, AircraftTypeId
from charteros.domain.airports import Airport, AirportId
from charteros.domain.missions import MissionId
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.normalization import PricingConfidence
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import (
    MatchingCandidateSnapshot,
    MatchingProfileId,
    MatchingReferenceProfile,
)
from charteros.repositioning import (
    BaselineEmptyLeg,
    FeasibleInsertion,
    QuotedFutureLeg,
    RepositionReasonCode,
    StructuralEmptyLeg,
    evaluate_baseline,
    evaluate_insertion,
    maximum_margin_matching,
)

BASE = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
EUR = Currency("EUR")
USD = Currency("USD")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _airport(value: int, *, lat: str, lon: str, icao: str) -> Airport:
    return Airport(
        AirportId(_id(value)),
        icao=icao,
        iata=None,
        latitude=Decimal(lat),
        longitude=Decimal(lon),
        timezone="UTC",
        runway_metadata={},
        curfew_metadata={},
        operational_flags=(),
    )


def _snapshot() -> MatchingCandidateSnapshot:
    aircraft_id = AircraftId(_id(100))
    operator_id = OperatorId(_id(200))
    aircraft_type_id = AircraftTypeId(_id(300))
    return MatchingCandidateSnapshot(
        aircraft_id=aircraft_id,
        operator_id=operator_id,
        aircraft_type_id=aircraft_type_id,
        seat_capacity=10,
        range_nm=3000,
        aircraft_status=AircraftStatus.ACTIVE,
        verification_status=VerificationStatus.VERIFIED,
        insurance_status=InsuranceStatus.VALID,
        commercial_status=CommercialStatus.ACTIVE,
        position=None,
        availability=None,
        reference_profile=MatchingReferenceProfile(
            id=MatchingProfileId(_id(400)),
            aircraft_type_id=aircraft_type_id,
            cruise_speed_kts=400,
            operating_cost_per_hour=Money(120_000, EUR),
            max_reposition_nm=1500,
            turnaround_buffer_minutes=30,
            source="pr18-test",
            provenance={},
            recorded_at=BASE,
        ),
    )


def _structural(seed: int = 1) -> StructuralEmptyLeg:
    return StructuralEmptyLeg(
        aircraft_id=_id(100),
        operator_id=_id(200),
        previous_booking_id=_id(1000 + seed),
        previous_mission_id=_id(2000 + seed),
        next_booking_id=_id(3000 + seed),
        next_mission_id=_id(4000 + seed),
        from_airport_id=_id(10),
        from_icao="AAAA",
        to_airport_id=_id(11),
        to_icao="BBBB",
        window_start=BASE + timedelta(hours=2),
        window_end=BASE + timedelta(hours=9),
        gap_minutes=420,
        evidence_kind="between_planned_bookings",
    )


def _opportunity(
    *,
    mission_seed: int = 1,
    currency: Currency = EUR,
    revenue_minor: int = 2_000_000,
) -> QuotedFutureLeg:
    return QuotedFutureLeg(
        mission_id=MissionId(_id(5000 + mission_seed)),
        rfq_id=RfqId(_id(6000 + mission_seed)),
        quote_id=QuoteId(_id(7000 + mission_seed)),
        aircraft_id=AircraftId(_id(100)),
        operator_id=OperatorId(_id(200)),
        origin_airport_id=AirportId(_id(10)),
        destination_airport_id=AirportId(_id(12)),
        departure_window=TimeRange(
            BASE + timedelta(hours=3),
            BASE + timedelta(hours=5),
        ),
        passenger_count=6,
        revenue=Money(revenue_minor, currency),
        worst_case_revenue=Money(revenue_minor, currency),
        totals_complete=True,
        pricing_confidence=PricingConfidence.HIGH,
    )


def test_reposition_policy_preserves_continuity_and_computes_incremental_margin() -> None:
    snapshot = _snapshot()
    from_airport = _airport(10, lat="40.00000", lon="20.00000", icao="AAAA")
    continuity = _airport(11, lat="41.00000", lon="21.00000", icao="BBBB")
    destination = _airport(12, lat="40.50000", lon="20.50000", icao="CCCC")

    baseline_eval = evaluate_baseline(
        structural=_structural(),
        candidate=snapshot,
        from_airport=from_airport,
        continuity_airport=continuity,
    )
    assert baseline_eval.baseline is not None

    insertion_eval = evaluate_insertion(
        baseline=baseline_eval.baseline,
        candidate=snapshot,
        opportunity=_opportunity(),
        from_airport=from_airport,
        mission_origin=from_airport,
        mission_destination=destination,
        continuity_airport=continuity,
    )
    insertion = insertion_eval.insertion
    assert insertion is not None
    assert insertion.margin.amount_minor > 0
    assert insertion.continuity_ready_at <= insertion.empty_leg.structural.window_end
    assert insertion.opportunity_cost.amount_minor == max(
        0,
        insertion.reposition_cost.amount_minor
        - insertion.empty_leg.baseline_reposition_cost.amount_minor,
    )
    assert insertion.margin == (
        insertion.revenue
        - insertion.revenue_leg_operating_cost
        - insertion.opportunity_cost
    )


def test_reposition_policy_fails_closed_on_currency_mismatch() -> None:
    snapshot = _snapshot()
    from_airport = _airport(10, lat="40.00000", lon="20.00000", icao="AAAA")
    continuity = _airport(11, lat="41.00000", lon="21.00000", icao="BBBB")
    destination = _airport(12, lat="40.50000", lon="20.50000", icao="CCCC")
    baseline = evaluate_baseline(
        structural=_structural(),
        candidate=snapshot,
        from_airport=from_airport,
        continuity_airport=continuity,
    ).baseline
    assert baseline is not None

    result = evaluate_insertion(
        baseline=baseline,
        candidate=snapshot,
        opportunity=_opportunity(currency=USD),
        from_airport=from_airport,
        mission_origin=from_airport,
        mission_destination=destination,
        continuity_airport=continuity,
    )
    assert result.insertion is None
    assert result.reasons == (RepositionReasonCode.CURRENCY_MISMATCH,)


def _solver_candidate(
    *,
    empty_seed: int,
    mission_seed: int,
    margin_minor: int,
) -> FeasibleInsertion:
    structural = _structural(empty_seed)
    opportunity = _opportunity(
        mission_seed=mission_seed,
        revenue_minor=margin_minor + 100_000,
    )
    zero = Money(0, EUR)
    margin = Money(margin_minor, EUR)
    baseline = BaselineEmptyLeg(
        structural=structural,
        baseline_distance_tenths_nm=100,
        baseline_minutes=10,
        baseline_reposition_cost=Money(10_000, EUR),
    )
    return FeasibleInsertion(
        empty_leg=baseline,
        opportunity=opportunity,
        scheduled_departure=opportunity.departure_window.start,
        continuity_ready_at=opportunity.departure_window.start + timedelta(hours=1),
        pre_reposition_distance_tenths_nm=0,
        revenue_distance_tenths_nm=100,
        post_reposition_distance_tenths_nm=100,
        pre_reposition_minutes=0,
        revenue_minutes=30,
        post_reposition_minutes=15,
        reposition_cost=zero,
        revenue_leg_operating_cost=Money(100_000, EUR),
        revenue=opportunity.revenue,
        gross_margin=margin,
        opportunity_cost=zero,
        margin=margin,
    )


def test_min_cost_flow_matching_beats_local_greedy_choice() -> None:
    candidates = (
        _solver_candidate(empty_seed=1, mission_seed=1, margin_minor=100),
        _solver_candidate(empty_seed=1, mission_seed=2, margin_minor=99),
        _solver_candidate(empty_seed=2, mission_seed=1, margin_minor=98),
    )
    selected = maximum_margin_matching(candidates)

    assert len(selected) == 2
    pairs = {
        (
            item.empty_leg.structural.previous_booking_id,
            item.opportunity.mission_id.value,
        )
        for item in selected
    }
    assert pairs == {
        (_id(1001), _id(5002)),
        (_id(1002), _id(5001)),
    }
    assert sum(item.margin.amount_minor for item in selected) == 197
