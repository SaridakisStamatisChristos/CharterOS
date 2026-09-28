from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from charteros.domain.aircraft import (
    AircraftId,
    AircraftStatus,
    AircraftTypeId,
    AvailabilityRecordId,
    AvailabilityStatus,
    PositionObservationId,
)
from charteros.domain.airports import AirportId
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import TypedId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange

POLICY_VERSION = "matching-v1"
REFERENCE_CURRENCY = Currency("EUR")
RANGE_RESERVE_PERCENT = 10
MAX_SCORE_BASIS_POINTS = 10_000
COMPONENT_POINTS = 2_500


class MatchingProfileId(TypedId):
    __slots__ = ()


class MatchReasonCode(StrEnum):
    FEASIBLE = "feasible"
    AIRCRAFT_ACTIVE = "aircraft_active"
    OPERATOR_VERIFIED = "operator_verified"
    OPERATOR_INSURANCE_VALID = "operator_insurance_valid"
    OPERATOR_COMMERCIAL_ACTIVE = "operator_commercial_active"
    CAPACITY_OK = "capacity_ok"
    RANGE_OK = "range_ok"
    AVAILABILITY_OK = "availability_ok"
    POSITION_KNOWN = "position_known"
    REFERENCE_PROFILE_OK = "reference_profile_ok"
    REFERENCE_CURRENCY_OK = "reference_currency_ok"
    REPOSITION_DISTANCE_OK = "reposition_distance_ok"
    REPOSITION_TIMING_OK = "reposition_timing_ok"
    BUDGET_WITHIN = "budget_within"
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_NOT_PROVIDED = "budget_not_provided"
    BUDGET_CURRENCY_MISMATCH = "budget_currency_mismatch"
    AIRCRAFT_INACTIVE = "aircraft_inactive"
    OPERATOR_UNVERIFIED = "operator_unverified"
    OPERATOR_INSURANCE_INVALID = "operator_insurance_invalid"
    OPERATOR_COMMERCIAL_INACTIVE = "operator_commercial_inactive"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"
    INSUFFICIENT_RANGE = "insufficient_range"
    NO_AVAILABILITY = "no_availability"
    NOT_AVAILABLE = "not_available"
    NO_POSITION = "no_position"
    NO_REFERENCE_PROFILE = "no_reference_profile"
    REFERENCE_CURRENCY_UNSUPPORTED = "reference_currency_unsupported"
    REPOSITION_TOO_FAR = "reposition_too_far"
    REPOSITION_TOO_LATE = "reposition_too_late"


class BudgetComparison(StrEnum):
    NOT_PROVIDED = "not_provided"
    WITHIN = "within"
    EXCEEDED = "exceeded"
    CURRENCY_MISMATCH = "currency_mismatch"


def ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _provenance(value: Mapping[str, object]) -> Mapping[str, object]:
    if any(not isinstance(key, str) for key in value):
        raise DomainValidationError("provenance keys must be strings")
    return MappingProxyType(dict(value))


def _source(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 128:
        raise DomainValidationError("source must contain 1 to 128 characters")
    return normalized


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    id: PositionObservationId
    aircraft_id: AircraftId
    airport_id: AirportId | None
    latitude: Decimal
    longitude: Decimal
    event_time: datetime
    recorded_at: datetime
    source: str
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if not Decimal("-90") <= self.latitude <= Decimal("90"):
            raise DomainValidationError("position latitude must be between -90 and 90")
        if not Decimal("-180") <= self.longitude <= Decimal("180"):
            raise DomainValidationError("position longitude must be between -180 and 180")
        object.__setattr__(self, "event_time", ensure_utc(self.event_time, field_name="event_time"))
        object.__setattr__(
            self, "recorded_at", ensure_utc(self.recorded_at, field_name="recorded_at")
        )
        object.__setattr__(self, "source", _source(self.source))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class AvailabilitySnapshot:
    id: AvailabilityRecordId
    aircraft_id: AircraftId
    interval: TimeRange
    status: AvailabilityStatus
    recorded_at: datetime
    source: str
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AvailabilityStatus(self.status))
        object.__setattr__(
            self, "recorded_at", ensure_utc(self.recorded_at, field_name="recorded_at")
        )
        object.__setattr__(self, "source", _source(self.source))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class MatchingReferenceProfile:
    id: MatchingProfileId
    aircraft_type_id: AircraftTypeId
    cruise_speed_kts: int
    operating_cost_per_hour: Money
    max_reposition_nm: int
    turnaround_buffer_minutes: int
    source: str
    provenance: Mapping[str, object]
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.cruise_speed_kts <= 0:
            raise DomainValidationError("cruise_speed_kts must be positive")
        if self.operating_cost_per_hour.amount_minor <= 0:
            raise DomainValidationError("operating cost per hour must be positive")
        if self.max_reposition_nm <= 0:
            raise DomainValidationError("max_reposition_nm must be positive")
        if self.turnaround_buffer_minutes < 0:
            raise DomainValidationError("turnaround_buffer_minutes cannot be negative")
        object.__setattr__(self, "source", _source(self.source))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        object.__setattr__(
            self, "recorded_at", ensure_utc(self.recorded_at, field_name="recorded_at")
        )


@dataclass(frozen=True, slots=True)
class MatchingCandidateSnapshot:
    aircraft_id: AircraftId
    operator_id: OperatorId
    aircraft_type_id: AircraftTypeId
    seat_capacity: int
    range_nm: int
    aircraft_status: AircraftStatus
    verification_status: VerificationStatus
    insurance_status: InsuranceStatus
    commercial_status: CommercialStatus
    position: PositionSnapshot | None
    availability: AvailabilitySnapshot | None
    reference_profile: MatchingReferenceProfile | None


@dataclass(frozen=True, slots=True)
class MatchDraft:
    aircraft_id: AircraftId
    operator_id: OperatorId
    aircraft_type_id: AircraftTypeId
    position: PositionSnapshot
    availability: AvailabilitySnapshot
    reference_profile: MatchingReferenceProfile
    route_distance_tenths_nm: int
    required_range_nm: int
    reposition_distance_tenths_nm: int
    route_minutes: int
    reposition_minutes: int
    timing_buffer_minutes: int
    schedule_risk_basis_points: int
    estimated_operating_cost: Money
    budget_comparison: BudgetComparison
    budget_delta_minor: int | None
    reason_codes: tuple[MatchReasonCode, ...]


@dataclass(frozen=True, slots=True)
class ScoreDecomposition:
    method: str
    total_basis_points: int
    deadhead_points: int
    operating_cost_points: int
    timing_buffer_points: int
    schedule_risk_points: int


@dataclass(frozen=True, slots=True)
class RankedMatch:
    rank: int
    draft: MatchDraft
    score: ScoreDecomposition


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    draft: MatchDraft | None
    rejection_reasons: tuple[MatchReasonCode, ...]


@dataclass(frozen=True, slots=True)
class MatchingDecision:
    policy_version: str
    reference_currency: Currency
    known_as_of: datetime
    position_event_cutoff: datetime
    route_distance_tenths_nm: int
    required_range_nm: int
    candidate_count: int
    feasible_count: int
    rejection_summary: Mapping[MatchReasonCode, int]
    matches: tuple[RankedMatch, ...]
