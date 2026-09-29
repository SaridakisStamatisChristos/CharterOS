from __future__ import annotations

import json
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import (
    flight_minutes,
    haversine_distance_tenths_nm,
    required_range_nm,
)
from charteros.simulation.sampler import DeterministicSampler
from charteros.simulation.types import (
    PPM,
    SIMULATOR_POLICY_VERSION,
    DemandCurvePoint,
    MarketSimulation,
    MarketSimulationConfig,
    SimulatedCompletedMission,
    SimulatedConversion,
    SimulatedDemand,
    SimulatedEmptyLeg,
    SimulatedOperatorDecision,
    SimulatedQuote,
    SimulatedQuoteRevision,
    SimulatorAirport,
    SimulatorOperator,
)


def _canonical(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Currency):
        return value.code
    if isinstance(value, Money):
        return {"amount_minor": value.amount_minor, "currency": value.currency.code}
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        ordered = sorted(value.items(), key=lambda item: str(item[0]))
        return {str(key): _canonical(item) for key, item in ordered}
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    return value


def canonical_json(value: object) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_canonical(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _id(scenario_id: UUID, kind: str, key: str) -> UUID:
    return uuid5(scenario_id, f"{kind}:{key}")


def _mul_ppm(value: int, multiplier_ppm: int) -> int:
    if value < 0 or multiplier_ppm < 0:
        raise DomainValidationError("fixed-point multiplication inputs cannot be negative")
    return (value * multiplier_ppm + PPM // 2) // PPM


def _signed_volatility(
    sampler: DeterministicSampler,
    *,
    label: str,
    max_abs_ppm: int,
) -> int:
    if max_abs_ppm == 0:
        return 0
    return sampler.integer(label, lower=-max_abs_ppm, upper=max_abs_ppm)


def _price_with_volatility(base_minor: int, seasonal_ppm: int, volatility_ppm: int) -> int:
    seasonal = _mul_ppm(base_minor, seasonal_ppm)
    multiplier = PPM + volatility_ppm
    if multiplier <= 0:
        multiplier = 1
    return max(1, _mul_ppm(seasonal, multiplier))


def _airport_map(config: MarketSimulationConfig) -> dict[str, SimulatorAirport]:
    return {item.icao: item for item in config.airports}


def _distance(
    airports: dict[str, SimulatorAirport],
    from_icao: str,
    to_icao: str,
) -> int:
    if from_icao == to_icao:
        return 0
    start = airports[from_icao]
    end = airports[to_icao]
    return haversine_distance_tenths_nm(
        start.latitude,
        start.longitude,
        end.latitude,
        end.longitude,
    )


def _scenario_namespace(seed: int, reference_digest: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"charteros:{SIMULATOR_POLICY_VERSION}:{seed}:{reference_digest}",
    )


def _validate_runtime_bound(config: MarketSimulationConfig) -> None:
    maximum_multiplier = PPM + config.demand_noise_ppm
    worst_case = 0
    for route in config.routes:
        peak_month = max(route.monthly_demand_ppm)
        route_daily = _mul_ppm(route.base_daily_demand * PPM, peak_month)
        route_daily = _mul_ppm(route_daily, maximum_multiplier)
        worst_case += (route_daily + PPM - 1) // PPM * config.days
    if worst_case > config.max_generated_demands:
        raise DomainValidationError(
            "configured deterministic demand upper bound exceeds max_generated_demands"
        )


def _generate_demand(
    *,
    config: MarketSimulationConfig,
    sampler: DeterministicSampler,
    scenario_id: UUID,
) -> tuple[tuple[DemandCurvePoint, ...], tuple[SimulatedDemand, ...]]:
    curves: list[DemandCurvePoint] = []
    demands: list[SimulatedDemand] = []
    for day_index in range(config.days):
        day_start = config.start_at + timedelta(days=day_index)
        for route in sorted(config.routes, key=lambda item: item.key):
            seasonal_ppm = route.monthly_demand_ppm[day_start.month - 1]
            noise_delta = sampler.integer(
                f"demand-noise:{route.key}:{day_index}",
                lower=-config.demand_noise_ppm,
                upper=config.demand_noise_ppm,
            )
            noise_ppm = PPM + noise_delta
            expected_ppm = route.base_daily_demand * PPM
            expected_ppm = _mul_ppm(expected_ppm, seasonal_ppm)
            expected_ppm = _mul_ppm(expected_ppm, noise_ppm)
            count, remainder = divmod(expected_ppm, PPM)
            if sampler.probability_hit(
                f"demand-fraction:{route.key}:{day_index}",
                remainder,
            ):
                count += 1
            curves.append(
                DemandCurvePoint(
                    scenario_id=scenario_id,
                    route_key=route.key,
                    day_start=day_start,
                    seasonal_multiplier_ppm=seasonal_ppm,
                    noise_multiplier_ppm=noise_ppm,
                    generated_demand=count,
                )
            )
            for local_index in range(count):
                demand_key = f"{route.key}:{day_index}:{local_index}"
                demand_id = _id(scenario_id, "demand", demand_key)
                created_minute = sampler.integer(
                    f"demand-created-minute:{demand_key}", lower=0, upper=23 * 60 + 59
                )
                created_at = day_start + timedelta(minutes=created_minute)
                lead_hours = sampler.integer(
                    f"demand-lead-hours:{demand_key}",
                    lower=route.lead_time_min_hours,
                    upper=route.lead_time_max_hours,
                )
                departure_start = created_at + timedelta(hours=lead_hours)
                passenger_count = sampler.integer(
                    f"demand-passengers:{demand_key}",
                    lower=route.passenger_min,
                    upper=route.passenger_max,
                )
                demands.append(
                    SimulatedDemand(
                        scenario_id=scenario_id,
                        demand_id=demand_id,
                        route_key=route.key,
                        origin_icao=route.origin_icao,
                        destination_icao=route.destination_icao,
                        currency=route.currency,
                        passenger_count=passenger_count,
                        created_at=created_at,
                        departure_window=TimeRange(
                            departure_start,
                            departure_start + timedelta(minutes=config.departure_window_minutes),
                        ),
                    )
                )
                if len(demands) > config.max_generated_demands:
                    raise DomainValidationError("generated demand exceeded max_generated_demands")
    demands.sort(key=lambda item: (item.departure_window.start, item.demand_id.hex))
    return tuple(curves), tuple(demands)


class _OperatorState:
    __slots__ = ("available_at", "location_icao")

    def __init__(self, *, available_at: datetime, location_icao: str) -> None:
        self.available_at = available_at
        self.location_icao = location_icao


def _schedule_for(
    *,
    operator: SimulatorOperator,
    state: _OperatorState,
    demand: SimulatedDemand,
    airports: dict[str, SimulatorAirport],
) -> tuple[bool, str, int, int, datetime]:
    if operator.currency != demand.currency:
        return False, "currency_mismatch", 0, 0, demand.departure_window.start
    if demand.passenger_count > operator.seat_capacity:
        return False, "insufficient_capacity", 0, 0, demand.departure_window.start
    revenue_distance = _distance(airports, demand.origin_icao, demand.destination_icao)
    if required_range_nm(revenue_distance) > operator.range_nm:
        return False, "insufficient_route_range", 0, 0, demand.departure_window.start
    reposition_distance = _distance(airports, state.location_icao, demand.origin_icao)
    if reposition_distance > operator.max_reposition_nm * 10:
        return False, "reposition_too_far", reposition_distance, 0, demand.departure_window.start
    if required_range_nm(reposition_distance) > operator.range_nm:
        return False, "reposition_range", reposition_distance, 0, demand.departure_window.start
    reposition_minutes = flight_minutes(reposition_distance, operator.cruise_speed_kts)
    reposition_buffer = operator.turnaround_minutes if reposition_distance > 0 else 0
    ready_at = state.available_at + timedelta(minutes=reposition_minutes + reposition_buffer)
    scheduled_departure = max(demand.departure_window.start, ready_at)
    if scheduled_departure >= demand.departure_window.end:
        return (
            False,
            "reposition_too_late",
            reposition_distance,
            reposition_minutes,
            scheduled_departure,
        )
    return True, "feasible", reposition_distance, reposition_minutes, scheduled_departure


def simulate_market(
    *,
    seed: int,
    config: MarketSimulationConfig,
) -> MarketSimulation:
    """Generate a deterministic synthetic market scenario without mutating production state."""

    _validate_runtime_bound(config)
    reference_digest = digest_canonical(config)
    scenario_id = _scenario_namespace(seed, reference_digest)
    sampler = DeterministicSampler(seed=seed, policy_version=SIMULATOR_POLICY_VERSION)
    airports = _airport_map(config)
    routes = {item.key: item for item in config.routes}
    operators = {item.key: item for item in config.operators}
    states = {
        item.key: _OperatorState(available_at=config.start_at, location_icao=item.home_icao)
        for item in config.operators
    }

    demand_curve, demands = _generate_demand(
        config=config,
        sampler=sampler,
        scenario_id=scenario_id,
    )
    decisions: list[SimulatedOperatorDecision] = []
    quotes: list[SimulatedQuote] = []
    conversions: list[SimulatedConversion] = []
    empty_legs: list[SimulatedEmptyLeg] = []
    completed: list[SimulatedCompletedMission] = []

    for demand in demands:
        route = routes[demand.route_key]
        quote_schedule: dict[UUID, tuple[str, int, int, datetime, str]] = {}
        demand_quotes: list[SimulatedQuote] = []
        for operator in sorted(config.operators, key=lambda item: item.key):
            state = states[operator.key]
            (
                feasible,
                schedule_reason,
                reposition_distance,
                reposition_minutes,
                scheduled_departure,
            ) = _schedule_for(
                operator=operator,
                state=state,
                demand=demand,
                airports=airports,
            )
            acceptance_probability = operator.acceptance_probability_ppm if feasible else 0
            acceptance_draw = sampler.sample_ppm(
                f"operator-accept:{demand.demand_id}:{operator.key}"
            )
            acceptance_hit = acceptance_draw < acceptance_probability
            quote_response_draw: int | None = None
            quote_responded = False
            if acceptance_hit:
                quote_response_draw = sampler.sample_ppm(
                    f"quote-response:{demand.demand_id}:{operator.key}"
                )
                quote_responded = quote_response_draw < operator.quote_response_probability_ppm
            reason = (
                schedule_reason
                if not feasible
                else ("accepted" if acceptance_hit else "rejected")
            )
            decision_time = min(
                demand.departure_window.start,
                demand.created_at
                + timedelta(
                    minutes=sampler.integer(
                        f"operator-decision-delay:{demand.demand_id}:{operator.key}",
                        lower=1,
                        upper=30,
                    )
                ),
            )
            decisions.append(
                SimulatedOperatorDecision(
                    scenario_id=scenario_id,
                    demand_id=demand.demand_id,
                    operator_key=operator.key,
                    accepted=acceptance_hit,
                    reason=reason,
                    acceptance_probability_ppm=acceptance_probability,
                    acceptance_draw_ppm=acceptance_draw,
                    quote_response_probability_ppm=(
                        operator.quote_response_probability_ppm if acceptance_hit else None
                    ),
                    quote_response_draw_ppm=quote_response_draw,
                    quote_responded=quote_responded,
                    decided_at=decision_time,
                )
            )
            if not quote_responded:
                continue

            quote_id = _id(scenario_id, "quote", f"{demand.demand_id}:{operator.key}")
            seasonal_price_ppm = route.monthly_price_ppm[demand.departure_window.start.month - 1]
            first_volatility = _signed_volatility(
                sampler,
                label=f"quote-volatility:{quote_id}:1",
                max_abs_ppm=operator.quote_volatility_ppm,
            )
            first_total = Money(
                _price_with_volatility(
                    route.base_quote_minor,
                    seasonal_price_ppm,
                    first_volatility,
                ),
                route.currency,
            )
            revisions = [
                SimulatedQuoteRevision(
                    revision_number=1,
                    quoted_total=first_total,
                    volatility_ppm=first_volatility,
                    created_at=min(
                        demand.departure_window.start,
                        decision_time
                        + timedelta(
                            minutes=sampler.integer(
                                f"quote-delay:{quote_id}:1", lower=1, upper=45
                            )
                        ),
                    ),
                )
            ]
            if sampler.probability_hit(
                f"quote-revision:{quote_id}", operator.quote_revision_probability_ppm
            ):
                second_volatility = _signed_volatility(
                    sampler,
                    label=f"quote-volatility:{quote_id}:2",
                    max_abs_ppm=operator.quote_volatility_ppm,
                )
                revisions.append(
                    SimulatedQuoteRevision(
                        revision_number=2,
                        quoted_total=Money(
                            _price_with_volatility(
                                route.base_quote_minor,
                                seasonal_price_ppm,
                                second_volatility,
                            ),
                            route.currency,
                        ),
                        volatility_ppm=second_volatility,
                        created_at=min(
                            demand.departure_window.start,
                            revisions[-1].created_at
                            + timedelta(
                                minutes=sampler.integer(
                                    f"quote-delay:{quote_id}:2", lower=1, upper=45
                                )
                            ),
                        ),
                    )
                )
            quote = SimulatedQuote(
                scenario_id=scenario_id,
                quote_id=quote_id,
                demand_id=demand.demand_id,
                operator_key=operator.key,
                aircraft_key=operator.aircraft_key,
                currency=route.currency,
                revisions=tuple(revisions),
            )
            demand_quotes.append(quote)
            quotes.append(quote)
            quote_schedule[quote_id] = (
                operator.key,
                reposition_distance,
                reposition_minutes,
                scheduled_departure,
                state.location_icao,
            )

        conversion_probability = config.conversion_probability_ppm if demand_quotes else 0
        conversion_draw = sampler.sample_ppm(f"conversion:{demand.demand_id}")
        converted = conversion_draw < conversion_probability
        selected: SimulatedQuote | None = None
        if converted:
            selected = min(
                demand_quotes,
                key=lambda item: (
                    item.current_revision.quoted_total.amount_minor,
                    item.quote_id.hex,
                ),
            )
        completion_probability: int | None = None
        completion_draw: int | None = None
        completed_hit = False
        if selected is not None:
            selected_operator = operators[selected.operator_key]
            completion_probability = selected_operator.completion_probability_ppm
            completion_draw = sampler.sample_ppm(
                f"completion:{demand.demand_id}:{selected_operator.key}"
            )
            completed_hit = completion_draw < completion_probability
        conversion_time = min(
            demand.departure_window.start,
            demand.created_at
            + timedelta(
                minutes=sampler.integer(
                    f"conversion-delay:{demand.demand_id}", lower=30, upper=180
                )
            ),
        )
        conversions.append(
            SimulatedConversion(
                scenario_id=scenario_id,
                demand_id=demand.demand_id,
                conversion_probability_ppm=conversion_probability,
                conversion_draw_ppm=conversion_draw,
                converted=selected is not None,
                selected_quote_id=selected.quote_id if selected is not None else None,
                selected_operator_key=selected.operator_key if selected is not None else None,
                completion_probability_ppm=completion_probability,
                completion_draw_ppm=completion_draw,
                completed=completed_hit,
                decided_at=conversion_time,
            )
        )
        if selected is None or not completed_hit:
            continue

        operator = operators[selected.operator_key]
        state = states[operator.key]
        _, reposition_distance, reposition_minutes, scheduled_departure, from_icao = quote_schedule[
            selected.quote_id
        ]
        preceding_empty_leg_id: UUID | None = None
        if from_icao != demand.origin_icao:
            empty_leg_id = _id(
                scenario_id,
                "empty-leg",
                f"{operator.key}:{demand.demand_id}:{from_icao}:{demand.origin_icao}",
            )
            reposition_departure = max(
                state.available_at,
                scheduled_departure
                - timedelta(minutes=reposition_minutes + operator.turnaround_minutes),
            )
            reposition_arrival = reposition_departure + timedelta(minutes=reposition_minutes)
            empty_legs.append(
                SimulatedEmptyLeg(
                    scenario_id=scenario_id,
                    empty_leg_id=empty_leg_id,
                    operator_key=operator.key,
                    aircraft_key=operator.aircraft_key,
                    from_icao=from_icao,
                    to_icao=demand.origin_icao,
                    distance_tenths_nm=reposition_distance,
                    departed_at=reposition_departure,
                    arrived_at=reposition_arrival,
                    before_demand_id=demand.demand_id,
                )
            )
            preceding_empty_leg_id = empty_leg_id

        revenue_distance = _distance(airports, demand.origin_icao, demand.destination_icao)
        revenue_minutes = flight_minutes(revenue_distance, operator.cruise_speed_kts)
        completed_at = scheduled_departure + timedelta(minutes=revenue_minutes)
        mission_id = _id(scenario_id, "mission", str(demand.demand_id))
        booking_id = _id(scenario_id, "booking", str(demand.demand_id))
        completed.append(
            SimulatedCompletedMission(
                scenario_id=scenario_id,
                mission_id=mission_id,
                booking_id=booking_id,
                demand_id=demand.demand_id,
                quote_id=selected.quote_id,
                operator_key=operator.key,
                aircraft_key=operator.aircraft_key,
                origin_icao=demand.origin_icao,
                destination_icao=demand.destination_icao,
                currency=demand.currency,
                quoted_total=selected.current_revision.quoted_total,
                departed_at=scheduled_departure,
                completed_at=completed_at,
                preceding_empty_leg_id=preceding_empty_leg_id,
            )
        )
        state.location_icao = demand.destination_icao
        state.available_at = completed_at + timedelta(minutes=operator.turnaround_minutes)

    period = TimeRange(config.start_at, config.start_at + timedelta(days=config.days))
    provisional = MarketSimulation(
        scenario_id=scenario_id,
        simulator_policy_version=SIMULATOR_POLICY_VERSION,
        seed=seed,
        reference_state_digest=reference_digest,
        scenario_digest="",
        configuration=config,
        period=period,
        demand_curve=demand_curve,
        demands=demands,
        operator_decisions=tuple(decisions),
        quotes=tuple(quotes),
        conversions=tuple(conversions),
        empty_legs=tuple(empty_legs),
        completed_missions=tuple(completed),
    )
    return replace(provisional, scenario_digest=digest_canonical(provisional))
