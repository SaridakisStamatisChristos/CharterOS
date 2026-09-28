from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from charteros.domain.aircraft import (
    AircraftId,
    AircraftStatus,
    AircraftTypeId,
    AvailabilityRecordId,
    AvailabilityStatus,
    PositionObservationId,
)
from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import (
    AvailabilitySnapshot,
    CandidateEvaluation,
    MatchReasonCode,
    MatchingCandidateSnapshot,
    MatchingProfileId,
    MatchingReferenceProfile,
    PositionSnapshot,
    evaluate_candidate,
    flight_minutes,
    operating_cost_for_minutes,
    rank_matches,
    required_range_nm,
)

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
DEPARTURE = NOW + timedelta(hours=8)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _mission(*, passengers: int = 50, budget: Money | None = None) -> Mission:
    return Mission(
        MissionId(_id(1)),
        buyer_id=OrganizationId(_id(2)),
        origin_airport_id=AirportId(_id(3)),
        destination_airport_id=AirportId(_id(4)),
        departure_window=TimeRange(DEPARTURE, DEPARTURE + timedelta(hours=2)),
        passenger_count=passengers,
        max_budget=budget,
        special_requirements=(),
        status=MissionStatus.OPEN,
        version=2,
    )


def _candidate(
    *,
    aircraft_value: int = 10,
    seats: int = 100,
    range_nm: int = 3_000,
    aircraft_status: AircraftStatus = AircraftStatus.ACTIVE,
    verification: VerificationStatus = VerificationStatus.VERIFIED,
    insurance: InsuranceStatus = InsuranceStatus.VALID,
    commercial: CommercialStatus = CommercialStatus.ACTIVE,
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE,
    include_position: bool = True,
    include_availability: bool = True,
    include_profile: bool = True,
    position_lon: Decimal = Decimal("23.9445"),
    hourly_cost_minor: int = 500_000,
) -> MatchingCandidateSnapshot:
    aircraft_id = AircraftId(_id(aircraft_value))
    aircraft_type_id = AircraftTypeId(_id(100 + aircraft_value))
    position = (
        PositionSnapshot(
            id=PositionObservationId(_id(200 + aircraft_value)),
            aircraft_id=aircraft_id,
            airport_id=None,
            latitude=Decimal("37.9364"),
            longitude=position_lon,
            event_time=NOW - timedelta(hours=1),
            recorded_at=NOW - timedelta(minutes=30),
            source="unit-test",
            provenance={"fixture": "position"},
        )
        if include_position
        else None
    )
    availability = (
        AvailabilitySnapshot(
            id=AvailabilityRecordId(_id(300 + aircraft_value)),
            aircraft_id=aircraft_id,
            interval=TimeRange(DEPARTURE, DEPARTURE + timedelta(hours=2)),
            status=availability_status,
            recorded_at=NOW - timedelta(minutes=20),
            source="unit-test",
            provenance={"fixture": "availability"},
        )
        if include_availability
        else None
    )
    profile = (
        MatchingReferenceProfile(
            id=MatchingProfileId(_id(400 + aircraft_value)),
            aircraft_type_id=aircraft_type_id,
            cruise_speed_kts=450,
            operating_cost_per_hour=Money(hourly_cost_minor, Currency("EUR")),
            max_reposition_nm=1_000,
            turnaround_buffer_minutes=45,
            source="unit-test-reference",
            provenance={"source_revision": 1},
            recorded_at=NOW - timedelta(minutes=10),
        )
        if include_profile
        else None
    )
    return MatchingCandidateSnapshot(
        aircraft_id=aircraft_id,
        operator_id=OperatorId(_id(500 + aircraft_value)),
        aircraft_type_id=aircraft_type_id,
        seat_capacity=seats,
        range_nm=range_nm,
        aircraft_status=aircraft_status,
        verification_status=verification,
        insurance_status=insurance,
        commercial_status=commercial,
        position=position,
        availability=availability,
        reference_profile=profile,
    )


def _evaluate(
    candidate: MatchingCandidateSnapshot,
    *,
    route_tenths: int = 10_000,
) -> CandidateEvaluation:
    return evaluate_candidate(
        mission=_mission(),
        candidate=candidate,
        origin_latitude=Decimal("37.9364"),
        origin_longitude=Decimal("23.9445"),
        route_distance_tenths_nm=route_tenths,
        decision_time=NOW,
    )


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_candidate(seats=49), MatchReasonCode.INSUFFICIENT_CAPACITY),
        (_candidate(range_nm=1_099), MatchReasonCode.INSUFFICIENT_RANGE),
        (
            _candidate(aircraft_status=AircraftStatus.MAINTENANCE),
            MatchReasonCode.AIRCRAFT_INACTIVE,
        ),
        (
            _candidate(verification=VerificationStatus.PENDING),
            MatchReasonCode.OPERATOR_UNVERIFIED,
        ),
        (
            _candidate(insurance=InsuranceStatus.EXPIRED),
            MatchReasonCode.OPERATOR_INSURANCE_INVALID,
        ),
        (
            _candidate(commercial=CommercialStatus.SUSPENDED),
            MatchReasonCode.OPERATOR_COMMERCIAL_INACTIVE,
        ),
        (_candidate(include_availability=False), MatchReasonCode.NO_AVAILABILITY),
        (
            _candidate(availability_status=AvailabilityStatus.RESERVED),
            MatchReasonCode.NOT_AVAILABLE,
        ),
        (_candidate(include_position=False), MatchReasonCode.NO_POSITION),
        (_candidate(include_profile=False), MatchReasonCode.NO_REFERENCE_PROFILE),
    ],
)
def test_required_hard_constraints_reject(
    candidate: MatchingCandidateSnapshot,
    reason: MatchReasonCode,
) -> None:
    evaluation = _evaluate(candidate)
    assert evaluation.draft is None
    assert reason in evaluation.rejection_reasons


def test_capacity_and_range_boundaries_are_inclusive() -> None:
    evaluation = _evaluate(_candidate(seats=50, range_nm=1_100))
    assert evaluation.rejection_reasons == ()
    assert evaluation.draft is not None
    assert evaluation.draft.required_range_nm == 1_100


def test_reposition_distance_and_timing_are_hard_filters() -> None:
    too_far = _candidate(position_lon=Decimal("-73.7781"))
    assert _evaluate(too_far).rejection_reasons == (
        MatchReasonCode.REPOSITION_TOO_FAR,
    )
    evaluation = evaluate_candidate(
        mission=_mission(),
        candidate=_candidate(),
        origin_latitude=Decimal("37.9364"),
        origin_longitude=Decimal("23.9445"),
        route_distance_tenths_nm=10_000,
        decision_time=DEPARTURE + timedelta(hours=1, minutes=30),
    )
    assert evaluation.rejection_reasons == (
        MatchReasonCode.REPOSITION_TOO_LATE,
    )


def test_exact_money_cost_and_explicit_budget_currency_semantics() -> None:
    evaluation = evaluate_candidate(
        mission=_mission(budget=Money(5_000_000, Currency("USD"))),
        candidate=_candidate(),
        origin_latitude=Decimal("37.9364"),
        origin_longitude=Decimal("23.9445"),
        route_distance_tenths_nm=10_000,
        decision_time=NOW,
    )
    assert evaluation.draft is not None
    assert MatchReasonCode.BUDGET_CURRENCY_MISMATCH in evaluation.draft.reason_codes
    assert evaluation.draft.estimated_operating_cost.currency == Currency("EUR")
    assert operating_cost_for_minutes(
        Money(100, Currency("EUR")), 61
    ).amount_minor == 102


def test_score_is_decomposed_and_tie_break_is_stable() -> None:
    first = _evaluate(_candidate(aircraft_value=10, hourly_cost_minor=400_000)).draft
    second = _evaluate(_candidate(aircraft_value=11, hourly_cost_minor=600_000)).draft
    assert first is not None and second is not None
    forward = rank_matches((second, first))
    reverse = rank_matches((first, second))
    assert [(item.draft.aircraft_id, item.score) for item in forward] == [
        (item.draft.aircraft_id, item.score) for item in reverse
    ]
    assert forward[0].score.total_basis_points == sum(
        (
            forward[0].score.deadhead_points,
            forward[0].score.operating_cost_points,
            forward[0].score.timing_buffer_points,
            forward[0].score.schedule_risk_points,
        )
    )


@given(st.integers(min_value=0, max_value=500_000))
def test_range_reserve_never_understates_route(route_tenths: int) -> None:
    required = required_range_nm(route_tenths)
    route_nm_ceiling = (route_tenths + 9) // 10
    assert required >= route_nm_ceiling


@given(
    st.integers(min_value=0, max_value=500_000),
    st.integers(min_value=1, max_value=1_500),
)
def test_flight_minutes_are_non_negative_and_monotone(
    distance_tenths: int,
    speed: int,
) -> None:
    minutes = flight_minutes(distance_tenths, speed)
    assert minutes >= 0
    assert flight_minutes(distance_tenths + 1, speed) >= minutes
