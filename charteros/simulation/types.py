from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange

PPM = 1_000_000
SIMULATOR_POLICY_VERSION = "market-sim-v1"
SYNTHETIC_EVIDENCE_KIND = "synthetic_market_simulation"
MAX_SIMULATION_DAYS = 366
MAX_AIRPORTS = 200
MAX_ROUTES = 100
MAX_OPERATORS = 100
MAX_GENERATED_DEMANDS = 50_000


def ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def validate_probability_ppm(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DomainValidationError(f"{field_name} must be an integer")
    if not 0 <= value <= PPM:
        raise DomainValidationError(f"{field_name} must be between 0 and {PPM}")


def _validate_key(value: str, *, field_name: str) -> None:
    if not value or len(value) > 64:
        raise DomainValidationError(f"{field_name} must contain 1 to 64 characters")


def _validate_icao(value: str, *, field_name: str) -> None:
    if len(value) != 4 or not value.isascii() or not value.isalnum() or not value.isupper():
        raise DomainValidationError(f"{field_name} must be a four-character uppercase ICAO code")


@dataclass(frozen=True, slots=True)
class SimulatorAirport:
    icao: str
    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        _validate_icao(self.icao, field_name="icao")
        if not Decimal("-90") <= self.latitude <= Decimal("90"):
            raise DomainValidationError("latitude must be between -90 and 90")
        if not Decimal("-180") <= self.longitude <= Decimal("180"):
            raise DomainValidationError("longitude must be between -180 and 180")


@dataclass(frozen=True, slots=True)
class MarketRoute:
    key: str
    origin_icao: str
    destination_icao: str
    currency: Currency
    base_daily_demand: int
    base_quote_minor: int
    passenger_min: int
    passenger_max: int
    lead_time_min_hours: int
    lead_time_max_hours: int
    monthly_demand_ppm: tuple[int, ...] = (PPM,) * 12
    monthly_price_ppm: tuple[int, ...] = (PPM,) * 12

    def __post_init__(self) -> None:
        _validate_key(self.key, field_name="route key")
        _validate_icao(self.origin_icao, field_name="origin_icao")
        _validate_icao(self.destination_icao, field_name="destination_icao")
        if self.origin_icao == self.destination_icao:
            raise DomainValidationError("route origin and destination must differ")
        if not isinstance(self.currency, Currency):
            raise DomainValidationError("route currency must be a Currency")
        if not 0 <= self.base_daily_demand <= 1_000:
            raise DomainValidationError("base_daily_demand must be between 0 and 1000")
        if self.base_quote_minor <= 0:
            raise DomainValidationError("base_quote_minor must be positive")
        Money(self.base_quote_minor, self.currency)
        if self.passenger_min <= 0 or self.passenger_max < self.passenger_min:
            raise DomainValidationError("passenger bounds are invalid")
        if self.lead_time_min_hours < 0 or self.lead_time_max_hours < self.lead_time_min_hours:
            raise DomainValidationError("lead-time bounds are invalid")
        if len(self.monthly_demand_ppm) != 12:
            raise DomainValidationError("monthly_demand_ppm must contain exactly 12 monthly values")
        if len(self.monthly_price_ppm) != 12:
            raise DomainValidationError("monthly_price_ppm must contain exactly 12 monthly values")
        if any(not 0 <= value <= 2 * PPM for value in self.monthly_demand_ppm):
            raise DomainValidationError(
                f"monthly_demand_ppm values must be between 0 and {2 * PPM}"
            )
        if any(not 1 <= value <= 2 * PPM for value in self.monthly_price_ppm):
            raise DomainValidationError(f"monthly_price_ppm values must be between 1 and {2 * PPM}")


@dataclass(frozen=True, slots=True)
class SimulatorOperator:
    key: str
    aircraft_key: str
    home_icao: str
    currency: Currency
    seat_capacity: int
    cruise_speed_kts: int
    range_nm: int
    max_reposition_nm: int
    turnaround_minutes: int
    acceptance_probability_ppm: int
    quote_response_probability_ppm: int
    quote_revision_probability_ppm: int
    quote_volatility_ppm: int
    completion_probability_ppm: int

    def __post_init__(self) -> None:
        _validate_key(self.key, field_name="operator key")
        _validate_key(self.aircraft_key, field_name="aircraft key")
        _validate_icao(self.home_icao, field_name="home_icao")
        if not isinstance(self.currency, Currency):
            raise DomainValidationError("operator currency must be a Currency")
        if self.seat_capacity <= 0:
            raise DomainValidationError("seat_capacity must be positive")
        if self.cruise_speed_kts <= 0:
            raise DomainValidationError("cruise_speed_kts must be positive")
        if self.range_nm <= 0:
            raise DomainValidationError("range_nm must be positive")
        if self.max_reposition_nm < 0:
            raise DomainValidationError("max_reposition_nm cannot be negative")
        if self.turnaround_minutes < 0:
            raise DomainValidationError("turnaround_minutes cannot be negative")
        validate_probability_ppm(
            self.acceptance_probability_ppm,
            field_name="acceptance_probability_ppm",
        )
        validate_probability_ppm(
            self.quote_response_probability_ppm,
            field_name="quote_response_probability_ppm",
        )
        validate_probability_ppm(
            self.quote_revision_probability_ppm,
            field_name="quote_revision_probability_ppm",
        )
        validate_probability_ppm(
            self.completion_probability_ppm,
            field_name="completion_probability_ppm",
        )
        if not 0 <= self.quote_volatility_ppm < PPM:
            raise DomainValidationError(f"quote_volatility_ppm must be between 0 and {PPM - 1}")


@dataclass(frozen=True, slots=True)
class MarketSimulationConfig:
    start_at: datetime
    days: int
    airports: tuple[SimulatorAirport, ...]
    routes: tuple[MarketRoute, ...]
    operators: tuple[SimulatorOperator, ...]
    conversion_probability_ppm: int
    demand_noise_ppm: int = 150_000
    departure_window_minutes: int = 120
    max_generated_demands: int = MAX_GENERATED_DEMANDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_at", ensure_utc(self.start_at, field_name="start_at"))
        if not 1 <= self.days <= MAX_SIMULATION_DAYS:
            raise DomainValidationError(f"days must be between 1 and {MAX_SIMULATION_DAYS}")
        if not 1 <= len(self.routes) <= MAX_ROUTES:
            raise DomainValidationError(f"routes must contain between 1 and {MAX_ROUTES} items")
        if not 1 <= len(self.operators) <= MAX_OPERATORS:
            raise DomainValidationError(
                f"operators must contain between 1 and {MAX_OPERATORS} items"
            )
        if not 1 <= len(self.airports) <= MAX_AIRPORTS:
            raise DomainValidationError(f"airports must contain between 1 and {MAX_AIRPORTS} items")
        validate_probability_ppm(
            self.conversion_probability_ppm,
            field_name="conversion_probability_ppm",
        )
        if not 0 <= self.demand_noise_ppm <= PPM:
            raise DomainValidationError(f"demand_noise_ppm must be between 0 and {PPM}")
        if not 1 <= self.departure_window_minutes <= 24 * 60:
            raise DomainValidationError("departure_window_minutes must be between 1 and 1440")
        if not 1 <= self.max_generated_demands <= MAX_GENERATED_DEMANDS:
            raise DomainValidationError(
                f"max_generated_demands must be between 1 and {MAX_GENERATED_DEMANDS}"
            )

        airport_codes = [item.icao for item in self.airports]
        if len(set(airport_codes)) != len(airport_codes):
            raise DomainValidationError("simulator airport ICAO codes must be unique")
        airport_set = set(airport_codes)
        route_keys = [item.key for item in self.routes]
        if len(set(route_keys)) != len(route_keys):
            raise DomainValidationError("route keys must be unique")
        for route in self.routes:
            if route.origin_icao not in airport_set or route.destination_icao not in airport_set:
                raise DomainValidationError("every route airport must exist in airports")
        operator_keys = [item.key for item in self.operators]
        aircraft_keys = [item.aircraft_key for item in self.operators]
        if len(set(operator_keys)) != len(operator_keys):
            raise DomainValidationError("operator keys must be unique")
        if len(set(aircraft_keys)) != len(aircraft_keys):
            raise DomainValidationError("aircraft keys must be unique")
        for operator in self.operators:
            if operator.home_icao not in airport_set:
                raise DomainValidationError("every operator home airport must exist in airports")


@dataclass(frozen=True, slots=True)
class DemandCurvePoint:
    scenario_id: UUID
    route_key: str
    day_start: datetime
    seasonal_multiplier_ppm: int
    noise_multiplier_ppm: int
    generated_demand: int
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        if self.generated_demand < 0:
            raise DomainValidationError("generated_demand cannot be negative")
        if self.seasonal_multiplier_ppm < 0 or self.noise_multiplier_ppm < 0:
            raise DomainValidationError("demand multipliers cannot be negative")


@dataclass(frozen=True, slots=True)
class SimulatedDemand:
    scenario_id: UUID
    demand_id: UUID
    route_key: str
    origin_icao: str
    destination_icao: str
    currency: Currency
    passenger_count: int
    created_at: datetime
    departure_window: TimeRange
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        if self.passenger_count <= 0:
            raise DomainValidationError("passenger_count must be positive")
        created = ensure_utc(self.created_at, field_name="created_at")
        object.__setattr__(self, "created_at", created)
        if created > self.departure_window.start:
            raise DomainValidationError(
                "demand cannot be created after its departure window starts"
            )


@dataclass(frozen=True, slots=True)
class SimulatedOperatorDecision:
    scenario_id: UUID
    demand_id: UUID
    operator_key: str
    accepted: bool
    reason: str
    acceptance_probability_ppm: int
    acceptance_draw_ppm: int
    quote_response_probability_ppm: int | None
    quote_response_draw_ppm: int | None
    quote_responded: bool
    decided_at: datetime
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        validate_probability_ppm(
            self.acceptance_probability_ppm, field_name="acceptance_probability_ppm"
        )
        if not 0 <= self.acceptance_draw_ppm < PPM:
            raise DomainValidationError("acceptance_draw_ppm must be between 0 and 999999")
        if self.accepted != (self.acceptance_draw_ppm < self.acceptance_probability_ppm):
            raise DomainValidationError("accepted must match acceptance probability and draw")
        if self.accepted:
            if self.quote_response_probability_ppm is None or self.quote_response_draw_ppm is None:
                raise DomainValidationError(
                    "accepted operator decision requires quote-response evidence"
                )
            validate_probability_ppm(
                self.quote_response_probability_ppm,
                field_name="quote_response_probability_ppm",
            )
            if not 0 <= self.quote_response_draw_ppm < PPM:
                raise DomainValidationError("quote_response_draw_ppm must be between 0 and 999999")
            expected_response = self.quote_response_draw_ppm < self.quote_response_probability_ppm
            if self.quote_responded != expected_response:
                raise DomainValidationError(
                    "quote_responded must match quote-response probability and draw"
                )
        elif (
            self.quote_response_probability_ppm is not None
            or self.quote_response_draw_ppm is not None
            or self.quote_responded
        ):
            raise DomainValidationError(
                "rejected operator decision cannot carry quote-response evidence"
            )
        object.__setattr__(self, "decided_at", ensure_utc(self.decided_at, field_name="decided_at"))


@dataclass(frozen=True, slots=True)
class SimulatedQuoteRevision:
    revision_number: int
    quoted_total: Money
    volatility_ppm: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.revision_number <= 0:
            raise DomainValidationError("revision_number must be positive")
        if not -PPM < self.volatility_ppm <= PPM:
            raise DomainValidationError(
                "volatility_ppm must be greater than -1,000,000 and <= 1,000,000"
            )
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, field_name="created_at"))


@dataclass(frozen=True, slots=True)
class SimulatedQuote:
    scenario_id: UUID
    quote_id: UUID
    demand_id: UUID
    operator_key: str
    aircraft_key: str
    currency: Currency
    revisions: tuple[SimulatedQuoteRevision, ...]
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        if not self.revisions:
            raise DomainValidationError("simulated quote must contain at least one revision")
        expected = tuple(range(1, len(self.revisions) + 1))
        actual = tuple(item.revision_number for item in self.revisions)
        if actual != expected:
            raise DomainValidationError("simulated quote revisions must be contiguous from 1")
        if any(item.quoted_total.currency != self.currency for item in self.revisions):
            raise DomainValidationError("simulated quote revision currency mismatch")
        if any(
            later.created_at < earlier.created_at
            for earlier, later in zip(self.revisions, self.revisions[1:], strict=False)
        ):
            raise DomainValidationError("simulated quote revisions must be time ordered")

    @property
    def current_revision(self) -> SimulatedQuoteRevision:
        return self.revisions[-1]


@dataclass(frozen=True, slots=True)
class SimulatedConversion:
    scenario_id: UUID
    demand_id: UUID
    conversion_probability_ppm: int
    conversion_draw_ppm: int
    converted: bool
    selected_quote_id: UUID | None
    selected_operator_key: str | None
    completion_probability_ppm: int | None
    completion_draw_ppm: int | None
    completed: bool
    decided_at: datetime
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        validate_probability_ppm(
            self.conversion_probability_ppm, field_name="conversion_probability_ppm"
        )
        if not 0 <= self.conversion_draw_ppm < PPM:
            raise DomainValidationError("conversion_draw_ppm must be between 0 and 999999")
        expected_conversion = self.conversion_draw_ppm < self.conversion_probability_ppm
        if self.converted != expected_conversion:
            raise DomainValidationError("converted must match conversion probability and draw")
        object.__setattr__(
            self, "decided_at", ensure_utc(self.decided_at, field_name="decided_at")
        )
        selected = self.selected_quote_id is not None and self.selected_operator_key is not None
        if self.converted != selected:
            raise DomainValidationError("conversion selection fields must match converted state")
        if self.converted:
            if self.completion_probability_ppm is None or self.completion_draw_ppm is None:
                raise DomainValidationError("converted outcome requires completion evidence")
            validate_probability_ppm(
                self.completion_probability_ppm, field_name="completion_probability_ppm"
            )
            if not 0 <= self.completion_draw_ppm < PPM:
                raise DomainValidationError("completion_draw_ppm must be between 0 and 999999")
            expected_completion = self.completion_draw_ppm < self.completion_probability_ppm
            if self.completed != expected_completion:
                raise DomainValidationError("completed must match completion probability and draw")
        elif (
            self.completion_probability_ppm is not None
            or self.completion_draw_ppm is not None
            or self.completed
        ):
            raise DomainValidationError("unconverted outcome cannot carry completion evidence")


@dataclass(frozen=True, slots=True)
class SimulatedEmptyLeg:
    scenario_id: UUID
    empty_leg_id: UUID
    operator_key: str
    aircraft_key: str
    from_icao: str
    to_icao: str
    distance_tenths_nm: int
    departed_at: datetime
    arrived_at: datetime
    before_demand_id: UUID
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        if self.from_icao == self.to_icao:
            raise DomainValidationError("empty leg must move between distinct airports")
        if self.distance_tenths_nm <= 0:
            raise DomainValidationError("empty-leg distance must be positive")
        departed = ensure_utc(self.departed_at, field_name="departed_at")
        arrived = ensure_utc(self.arrived_at, field_name="arrived_at")
        if arrived <= departed:
            raise DomainValidationError("empty leg must arrive after departure")
        object.__setattr__(self, "departed_at", departed)
        object.__setattr__(self, "arrived_at", arrived)


@dataclass(frozen=True, slots=True)
class SimulatedCompletedMission:
    scenario_id: UUID
    mission_id: UUID
    booking_id: UUID
    demand_id: UUID
    quote_id: UUID
    operator_key: str
    aircraft_key: str
    origin_icao: str
    destination_icao: str
    currency: Currency
    quoted_total: Money
    departed_at: datetime
    completed_at: datetime
    preceding_empty_leg_id: UUID | None
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        departed = ensure_utc(self.departed_at, field_name="departed_at")
        completed = ensure_utc(self.completed_at, field_name="completed_at")
        if completed <= departed:
            raise DomainValidationError("completed mission must complete after departure")
        if self.quoted_total.currency != self.currency:
            raise DomainValidationError("completed mission quoted_total currency mismatch")
        object.__setattr__(self, "departed_at", departed)
        object.__setattr__(self, "completed_at", completed)


@dataclass(frozen=True, slots=True)
class MarketSimulation:
    scenario_id: UUID
    simulator_policy_version: str
    seed: int
    reference_state_digest: str
    scenario_digest: str
    configuration: MarketSimulationConfig
    period: TimeRange
    demand_curve: tuple[DemandCurvePoint, ...]
    demands: tuple[SimulatedDemand, ...]
    operator_decisions: tuple[SimulatedOperatorDecision, ...]
    quotes: tuple[SimulatedQuote, ...]
    conversions: tuple[SimulatedConversion, ...]
    empty_legs: tuple[SimulatedEmptyLeg, ...]
    completed_missions: tuple[SimulatedCompletedMission, ...]
    synthetic: bool = field(default=True, init=False)
    evidence_kind: str = field(default=SYNTHETIC_EVIDENCE_KIND, init=False)
