from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.simulation import (
    PPM,
    SYNTHETIC_EVIDENCE_KIND,
    DeterministicSampler,
    MarketRoute,
    MarketSimulationConfig,
    SimulatedCompletedMission,
    SimulatorAirport,
    SimulatorOperator,
    canonical_json,
    digest_canonical,
    simulate_market,
)

EUR = Currency("EUR")
USD = Currency("USD")
START = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


def _seasonal(*, september: int = PPM, october: int = PPM) -> tuple[int, ...]:
    values = [PPM] * 12
    values[8] = september
    values[9] = october
    return tuple(values)


def _config() -> MarketSimulationConfig:
    airports = (
        SimulatorAirport("LGAV", Decimal("37.9364"), Decimal("23.9445")),
        SimulatorAirport("LGTS", Decimal("40.5197"), Decimal("22.9709")),
        SimulatorAirport("LGIR", Decimal("35.3397"), Decimal("25.1803")),
        SimulatorAirport("LGRP", Decimal("36.4054"), Decimal("28.0862")),
    )
    routes = (
        MarketRoute(
            key="ath-skg",
            origin_icao="LGAV",
            destination_icao="LGTS",
            currency=EUR,
            base_daily_demand=2,
            base_quote_minor=1_500_000,
            passenger_min=2,
            passenger_max=8,
            lead_time_min_hours=8,
            lead_time_max_hours=16,
            monthly_demand_ppm=_seasonal(september=1_500_000, october=500_000),
            monthly_price_ppm=_seasonal(september=1_100_000, october=900_000),
        ),
        MarketRoute(
            key="skg-her",
            origin_icao="LGTS",
            destination_icao="LGIR",
            currency=EUR,
            base_daily_demand=1,
            base_quote_minor=1_900_000,
            passenger_min=2,
            passenger_max=8,
            lead_time_min_hours=8,
            lead_time_max_hours=16,
            monthly_demand_ppm=_seasonal(september=1_250_000, october=750_000),
            monthly_price_ppm=_seasonal(),
        ),
    )
    operators = (
        SimulatorOperator(
            key="operator-eur",
            aircraft_key="sx-sim-001",
            home_icao="LGRP",
            currency=EUR,
            seat_capacity=10,
            cruise_speed_kts=450,
            range_nm=3_000,
            max_reposition_nm=1_500,
            turnaround_minutes=30,
            acceptance_probability_ppm=PPM,
            quote_response_probability_ppm=PPM,
            quote_revision_probability_ppm=PPM,
            quote_volatility_ppm=120_000,
            completion_probability_ppm=PPM,
        ),
        SimulatorOperator(
            key="operator-usd",
            aircraft_key="n-sim-001",
            home_icao="LGAV",
            currency=USD,
            seat_capacity=10,
            cruise_speed_kts=450,
            range_nm=3_000,
            max_reposition_nm=1_500,
            turnaround_minutes=30,
            acceptance_probability_ppm=PPM,
            quote_response_probability_ppm=PPM,
            quote_revision_probability_ppm=PPM,
            quote_volatility_ppm=120_000,
            completion_probability_ppm=PPM,
        ),
    )
    return MarketSimulationConfig(
        start_at=START,
        days=4,
        airports=airports,
        routes=routes,
        operators=operators,
        conversion_probability_ppm=PPM,
        demand_noise_ppm=0,
        departure_window_minutes=240,
    )


def test_same_seed_and_config_replay_exactly() -> None:
    first = simulate_market(seed=42, config=_config())
    second = simulate_market(seed=42, config=_config())

    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert first.scenario_digest == second.scenario_digest
    assert first.reference_state_digest == second.reference_state_digest
    assert first.configuration == _config()
    assert first.scenario_digest == digest_canonical(replace(first, scenario_digest=""))


def test_different_seed_changes_scenario_deterministically() -> None:
    first = simulate_market(seed=41, config=_config())
    second = simulate_market(seed=42, config=_config())
    replay = simulate_market(seed=41, config=_config())

    assert first == replay
    assert first.scenario_id != second.scenario_id
    assert first.scenario_digest != second.scenario_digest


def test_sampler_is_semantically_addressed_not_call_order_stateful() -> None:
    sampler = DeterministicSampler(seed=7, policy_version="test-v1")
    before = sampler.u64("demand:alpha")
    _ = sampler.u64("unrelated:new-sample")
    after = sampler.u64("demand:alpha")

    assert before == after


def test_seasonality_is_explicit_and_crosses_month_boundary() -> None:
    scenario = simulate_market(seed=9, config=_config())
    ath = [point for point in scenario.demand_curve if point.route_key == "ath-skg"]

    assert [point.day_start.date().isoformat() for point in ath] == [
        "2026-09-29",
        "2026-09-30",
        "2026-10-01",
        "2026-10-02",
    ]
    assert [point.seasonal_multiplier_ppm for point in ath] == [
        1_500_000,
        1_500_000,
        500_000,
        500_000,
    ]
    assert [point.generated_demand for point in ath] == [3, 3, 1, 1]


def test_quote_revisions_are_immutable_ordered_exact_money_and_single_currency() -> None:
    scenario = simulate_market(seed=21, config=_config())

    assert scenario.quotes
    assert all(quote.currency == EUR for quote in scenario.quotes)
    assert all(len(quote.revisions) == 2 for quote in scenario.quotes)
    for quote in scenario.quotes:
        assert [revision.revision_number for revision in quote.revisions] == [1, 2]
        assert all(revision.quoted_total.currency == EUR for revision in quote.revisions)
        assert all(
            isinstance(revision.quoted_total.amount_minor, int) for revision in quote.revisions
        )
        assert quote.revisions[0].created_at <= quote.revisions[1].created_at


def test_currency_mismatch_fails_closed_without_fx_or_cross_currency_ranking() -> None:
    scenario = simulate_market(seed=33, config=_config())
    usd_decisions = [
        decision
        for decision in scenario.operator_decisions
        if decision.operator_key == "operator-usd"
    ]

    assert usd_decisions
    assert all(not decision.accepted for decision in usd_decisions)
    assert all(decision.reason == "currency_mismatch" for decision in usd_decisions)
    assert all(quote.currency == EUR for quote in scenario.quotes)
    assert all(mission.currency == EUR for mission in scenario.completed_missions)


def test_completed_missions_are_causally_linked_and_repositioning_preserves_continuity() -> None:
    scenario = simulate_market(seed=71, config=_config())
    conversions = {item.demand_id: item for item in scenario.conversions}
    quotes = {item.quote_id: item for item in scenario.quotes}
    empty_legs = {item.empty_leg_id: item for item in scenario.empty_legs}

    assert scenario.completed_missions
    assert scenario.empty_legs
    previous_by_operator: dict[str, SimulatedCompletedMission] = {}
    for mission in scenario.completed_missions:
        conversion = conversions[mission.demand_id]
        assert conversion.converted is True
        assert conversion.completed is True
        assert conversion.selected_quote_id == mission.quote_id
        assert mission.quote_id in quotes
        assert conversion.decided_at <= mission.departed_at
        assert mission.departed_at < mission.completed_at
        if mission.preceding_empty_leg_id is not None:
            leg = empty_legs[mission.preceding_empty_leg_id]
            assert leg.before_demand_id == mission.demand_id
            assert leg.to_icao == mission.origin_icao
            assert leg.arrived_at <= mission.departed_at
        previous = previous_by_operator.get(mission.operator_key)
        if previous is not None:
            assert previous.completed_at <= mission.departed_at
            if previous.destination_icao != mission.origin_icao:
                assert mission.preceding_empty_leg_id is not None
                assert (
                    empty_legs[mission.preceding_empty_leg_id].from_icao
                    == previous.destination_icao
                )
        previous_by_operator[mission.operator_key] = mission


def test_every_simulation_record_is_explicitly_synthetic() -> None:
    scenario = simulate_market(seed=81, config=_config())

    assert scenario.synthetic is True
    assert scenario.evidence_kind == SYNTHETIC_EVIDENCE_KIND
    collections = (
        scenario.demand_curve,
        scenario.demands,
        scenario.operator_decisions,
        scenario.quotes,
        scenario.conversions,
        scenario.empty_legs,
        scenario.completed_missions,
    )
    for collection in collections:
        for item in collection:
            assert item.synthetic is True
            assert item.evidence_kind == SYNTHETIC_EVIDENCE_KIND
            assert item.scenario_id == scenario.scenario_id


def test_fixed_scenario_has_no_wall_clock_dependency() -> None:
    config = _config()
    first = simulate_market(seed=101, config=config)
    _irrelevant_wall_clock = datetime.now(UTC) + timedelta(days=1000)
    second = simulate_market(seed=101, config=config)

    assert first == second
    assert all(item.created_at >= config.start_at for item in first.demands)


def test_pathological_demand_configuration_is_rejected_before_generation() -> None:
    config = _config()
    explosive = replace(
        config.routes[0],
        base_daily_demand=1_000,
        monthly_demand_ppm=(2 * PPM,) * 12,
    )
    bounded = replace(
        config,
        days=366,
        routes=(explosive,),
        demand_noise_ppm=PPM,
        max_generated_demands=50_000,
    )

    with pytest.raises(DomainValidationError, match="upper bound"):
        simulate_market(seed=1, config=bounded)
