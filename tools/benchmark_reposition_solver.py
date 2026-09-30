from __future__ import annotations

import json
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID

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
from charteros.repositioning import (
    BaselineEmptyLeg,
    FeasibleInsertion,
    QuotedFutureLeg,
    StructuralEmptyLeg,
    maximum_margin_matching,
)

BASE = datetime(2026, 9, 30, tzinfo=UTC)
EUR = Currency("EUR")
AIRCRAFT_ID = AircraftId(UUID(int=1))
OPERATOR_ID = OperatorId(UUID(int=2))


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    left_nodes: int
    right_nodes: int

    @property
    def candidate_matches(self) -> int:
        return self.left_nodes * self.right_nodes


CASES = (
    BenchmarkCase(10, 100),
    BenchmarkCase(50, 500),
    BenchmarkCase(100, 2_000),
)


def _structural(index: int) -> StructuralEmptyLeg:
    return StructuralEmptyLeg(
        aircraft_id=AIRCRAFT_ID.value,
        operator_id=OPERATOR_ID.value,
        previous_booking_id=UUID(int=10_000 + index),
        previous_mission_id=UUID(int=20_000 + index),
        next_booking_id=UUID(int=30_000 + index),
        next_mission_id=UUID(int=40_000 + index),
        previous_origin_airport_id=UUID(int=50_000 + index),
        from_airport_id=UUID(int=60_000 + index),
        from_icao="AAAA",
        to_airport_id=UUID(int=70_000 + index),
        to_icao="BBBB",
        window_start=BASE + timedelta(minutes=index),
        window_end=BASE + timedelta(hours=12, minutes=index),
        gap_minutes=720,
        evidence_kind="benchmark",
    )


def _opportunity(index: int) -> QuotedFutureLeg:
    return QuotedFutureLeg(
        mission_id=MissionId(UUID(int=100_000 + index)),
        rfq_id=RfqId(UUID(int=200_000 + index)),
        quote_id=QuoteId(UUID(int=300_000 + index)),
        aircraft_id=AIRCRAFT_ID,
        operator_id=OPERATOR_ID,
        origin_airport_id=AirportId(UUID(int=400_000 + index)),
        destination_airport_id=AirportId(UUID(int=500_000 + index)),
        departure_window=TimeRange(
            BASE + timedelta(hours=1),
            BASE + timedelta(hours=2),
        ),
        passenger_count=1,
        revenue=Money(1_000_000 + index, EUR),
        worst_case_revenue=Money(1_000_000 + index, EUR),
        totals_complete=True,
        pricing_confidence=PricingConfidence.HIGH,
    )


def _candidates(case: BenchmarkCase) -> tuple[FeasibleInsertion, ...]:
    zero = Money.zero(EUR)
    baselines = tuple(
        BaselineEmptyLeg(
            structural=_structural(left),
            aircraft_available_at=BASE,
            previous_revenue_distance_tenths_nm=100,
            previous_revenue_minutes=10,
            baseline_distance_tenths_nm=100,
            baseline_minutes=10,
            baseline_reposition_cost=Money(1_000, EUR),
            baseline_reposition_feasible=True,
        )
        for left in range(case.left_nodes)
    )
    opportunities = tuple(_opportunity(right) for right in range(case.right_nodes))
    result: list[FeasibleInsertion] = []
    for left, baseline in enumerate(baselines):
        for right, opportunity in enumerate(opportunities):
            # Deterministic positive weights create enough competition to exercise reassignment
            # without making benchmark results dependent on randomness.
            margin_minor = 10_000 + ((left * 1_009 + right * 917) % 50_000)
            margin = Money(margin_minor, EUR)
            result.append(
                FeasibleInsertion(
                    empty_leg=baseline,
                    opportunity=opportunity,
                    scheduled_departure=BASE + timedelta(hours=1),
                    continuity_ready_at=BASE + timedelta(hours=2),
                    pre_reposition_distance_tenths_nm=0,
                    revenue_distance_tenths_nm=100,
                    post_reposition_distance_tenths_nm=100,
                    pre_reposition_minutes=0,
                    revenue_minutes=30,
                    post_reposition_minutes=15,
                    reposition_cost=zero,
                    revenue_leg_operating_cost=zero,
                    revenue=Money(margin_minor, EUR),
                    gross_margin=margin,
                    opportunity_cost=zero,
                    margin=margin,
                )
            )
    return tuple(result)


def _run(case: BenchmarkCase) -> dict[str, int | float | str]:
    tracemalloc.start()
    started = perf_counter()
    load_started = perf_counter()
    candidates = _candidates(case)
    load_ms = (perf_counter() - load_started) * 1_000

    solver_started = perf_counter()
    selected = maximum_margin_matching(candidates)
    solver_ms = (perf_counter() - solver_started) * 1_000
    total_ms = (perf_counter() - started) * 1_000
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "left_nodes": case.left_nodes,
        "right_nodes": case.right_nodes,
        "nodes": case.left_nodes + case.right_nodes,
        "edges": case.candidate_matches,
        "candidate_matches": len(candidates),
        "selected_assignments": len(selected),
        "data_load_ms": round(load_ms, 3),
        "solver_ms": round(solver_ms, 3),
        "total_latency_ms": round(total_ms, 3),
        "peak_memory_mib": round(peak_bytes / (1024 * 1024), 3),
        "data_load_scope": "synthetic immutable DTO construction; excludes PostgreSQL",
    }


def main() -> None:
    for case in CASES:
        print(json.dumps(_run(case), sort_keys=True))


if __name__ == "__main__":
    main()
