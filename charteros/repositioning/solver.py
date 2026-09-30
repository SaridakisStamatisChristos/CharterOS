from __future__ import annotations

from uuid import UUID

from charteros.domain.airports import AirportId
from charteros.domain.shared.money import Money
from charteros.repositioning.types import (
    CurrencyOptimizationPlan,
    FeasibleInsertion,
    RepositionAssignment,
)


def _left_key(item: FeasibleInsertion) -> tuple[UUID, UUID, UUID]:
    structural = item.empty_leg.structural
    return (
        item.opportunity.aircraft_id.value,
        structural.previous_booking_id,
        structural.next_booking_id,
    )


def _candidate_sort_key(item: FeasibleInsertion) -> tuple[object, ...]:
    structural = item.empty_leg.structural
    return (
        structural.window_start,
        item.opportunity.aircraft_id.value.hex,
        structural.previous_booking_id.hex,
        structural.next_booking_id.hex,
        item.opportunity.departure_window.start,
        item.opportunity.mission_id.value.hex,
        item.opportunity.quote_id.value.hex,
    )


def _maximum_weight_assignment(
    row_weights: list[dict[int, tuple[int, int]]],
    *,
    real_column_count: int,
) -> tuple[int, ...]:
    """Solve a deterministic rectangular maximum-weight assignment exactly.

    Each row receives either one real column or one zero-weight dummy column. Missing real edges
    have a positive minimization penalty, so an unmatched row always uses a dummy rather than
    consuming a mission it cannot actually serve.

    The implementation is the shortest-augmenting-path Hungarian algorithm. With PR18's hard
    bounds (at most 100 structural gaps and 2,000 missions), its dense worst case is bounded by
    O(L^2 * (R + L)) rather than repeatedly relaxing every residual edge.
    """

    row_count = len(row_weights)
    if row_count == 0 or real_column_count == 0:
        return ()

    column_count = real_column_count + row_count
    max_weight = max(
        (
            weight
            for row in row_weights
            for weight, _candidate_index in row.values()
        ),
        default=0,
    )
    infinity = (max_weight + 2) * (row_count + column_count + 2)

    # Hungarian uses one-based row/column indexing. p[column] is the assigned row.
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    assigned_row = [0] * (column_count + 1)
    predecessor_column = [0] * (column_count + 1)

    for row_number in range(1, row_count + 1):
        assigned_row[0] = row_number
        minimum_reduced_cost = [infinity] * (column_count + 1)
        used = [False] * (column_count + 1)
        current_column = 0

        while True:
            used[current_column] = True
            active_row = assigned_row[current_column]
            weights = row_weights[active_row - 1]
            delta = infinity
            next_column = 0

            for column_number in range(1, column_count + 1):
                if used[column_number]:
                    continue
                zero_based_column = column_number - 1
                if zero_based_column < real_column_count:
                    candidate = weights.get(zero_based_column)
                    # Dummy columns cost zero. Missing real edges cost +1 so they cannot steal a
                    # mission from a feasible edge when an unmatched dummy is always available.
                    cost = -candidate[0] if candidate is not None else 1
                else:
                    cost = 0

                reduced_cost = (
                    cost
                    - row_potential[active_row]
                    - column_potential[column_number]
                )
                if reduced_cost < minimum_reduced_cost[column_number]:
                    minimum_reduced_cost[column_number] = reduced_cost
                    predecessor_column[column_number] = current_column
                if minimum_reduced_cost[column_number] < delta:
                    delta = minimum_reduced_cost[column_number]
                    next_column = column_number

            for column_number in range(column_count + 1):
                if used[column_number]:
                    row_potential[assigned_row[column_number]] += delta
                    column_potential[column_number] -= delta
                else:
                    minimum_reduced_cost[column_number] -= delta

            current_column = next_column
            if assigned_row[current_column] == 0:
                break

        while True:
            previous_column = predecessor_column[current_column]
            assigned_row[current_column] = assigned_row[previous_column]
            current_column = previous_column
            if current_column == 0:
                break

    selected: list[int] = []
    for column_number in range(1, real_column_count + 1):
        row_number = assigned_row[column_number]
        if row_number == 0:
            continue
        candidate = row_weights[row_number - 1].get(column_number - 1)
        if candidate is not None:
            selected.append(candidate[1])
    selected.sort()
    return tuple(selected)


def maximum_margin_matching(
    candidates: tuple[FeasibleInsertion, ...],
) -> tuple[FeasibleInsertion, ...]:
    """Return the exact deterministic maximum-margin bipartite assignment.

    Each structural empty-leg window can accept at most one future mission and each mission can be
    assigned at most once. Margin remains the primary objective. The existing candidate-order tie
    encoding remains strictly smaller than one minor unit of aggregate margin.
    """

    if not candidates:
        return ()

    ordered = tuple(sorted(candidates, key=_candidate_sort_key))
    left_keys = tuple(
        sorted(
            {_left_key(item) for item in ordered},
            key=lambda item: tuple(value.hex for value in item),
        )
    )
    right_keys = tuple(
        sorted({item.opportunity.mission_id.value for item in ordered}, key=lambda item: item.hex)
    )
    left_index = {key: index for index, key in enumerate(left_keys)}
    right_index = {key: index for index, key in enumerate(right_keys)}

    max_assignments = min(len(left_keys), len(right_keys))
    # Preserve PR18's objective encoding: one minor unit of margin dominates every possible
    # aggregate candidate-order tie penalty.
    tie_scale = max_assignments * len(ordered) + 1
    row_weights: list[dict[int, tuple[int, int]]] = [dict() for _ in left_keys]

    for candidate_index, item in enumerate(ordered):
        margin_minor = item.margin.amount_minor
        if margin_minor <= 0:
            continue
        row = left_index[_left_key(item)]
        column = right_index[item.opportunity.mission_id.value]
        encoded_weight = margin_minor * tie_scale - candidate_index
        existing = row_weights[row].get(column)
        if existing is None or encoded_weight > existing[0]:
            # Parallel candidates for the same gap/mission pair are equivalent assignment edges;
            # retaining only the best encoded edge preserves the exact objective.
            row_weights[row][column] = (encoded_weight, candidate_index)

    if not any(row_weights):
        return ()

    selected_indexes = _maximum_weight_assignment(
        row_weights,
        real_column_count=len(right_keys),
    )
    return tuple(ordered[index] for index in selected_indexes)


def to_assignment(item: FeasibleInsertion) -> RepositionAssignment:
    structural = item.empty_leg.structural
    return RepositionAssignment(
        aircraft_id=item.opportunity.aircraft_id,
        operator_id=item.opportunity.operator_id,
        previous_booking_id=structural.previous_booking_id,
        next_booking_id=structural.next_booking_id,
        mission_id=item.opportunity.mission_id,
        quote_id=item.opportunity.quote_id,
        from_airport_id=AirportId(structural.from_airport_id),
        mission_origin_airport_id=item.opportunity.origin_airport_id,
        mission_destination_airport_id=item.opportunity.destination_airport_id,
        continuity_airport_id=AirportId(structural.to_airport_id),
        window_start=structural.window_start,
        window_end=structural.window_end,
        aircraft_available_at=item.empty_leg.aircraft_available_at,
        scheduled_departure=item.scheduled_departure,
        continuity_ready_at=item.continuity_ready_at,
        previous_revenue_distance_tenths_nm=(item.empty_leg.previous_revenue_distance_tenths_nm),
        previous_revenue_minutes=item.empty_leg.previous_revenue_minutes,
        baseline_reposition_distance_tenths_nm=item.empty_leg.baseline_distance_tenths_nm,
        pre_reposition_distance_tenths_nm=item.pre_reposition_distance_tenths_nm,
        revenue_distance_tenths_nm=item.revenue_distance_tenths_nm,
        post_reposition_distance_tenths_nm=item.post_reposition_distance_tenths_nm,
        baseline_reposition_cost=item.empty_leg.baseline_reposition_cost,
        baseline_reposition_feasible=item.empty_leg.baseline_reposition_feasible,
        reposition_cost=item.reposition_cost,
        revenue_leg_operating_cost=item.revenue_leg_operating_cost,
        revenue=item.revenue,
        gross_margin=item.gross_margin,
        opportunity_cost=item.opportunity_cost,
        margin=item.margin,
        pricing_confidence=item.opportunity.pricing_confidence,
        totals_complete=item.opportunity.totals_complete,
    )


def build_currency_plan(
    candidates: tuple[FeasibleInsertion, ...],
) -> CurrencyOptimizationPlan:
    if not candidates:
        raise ValueError("currency plan requires at least one candidate")
    currency = candidates[0].currency
    if any(item.currency != currency for item in candidates):
        raise ValueError("currency plan cannot mix currencies")
    selected = maximum_margin_matching(candidates)
    assignments = tuple(to_assignment(item) for item in selected)
    total = Money.zero(currency)
    for item in assignments:
        total = total + item.margin
    return CurrencyOptimizationPlan(
        currency=currency,
        candidate_count=len(candidates),
        assignment_count=len(assignments),
        total_margin=total,
        assignments=assignments,
    )
