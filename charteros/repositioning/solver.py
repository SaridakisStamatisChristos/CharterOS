from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from charteros.domain.airports import AirportId
from charteros.domain.shared.money import Money
from charteros.repositioning.types import (
    CurrencyOptimizationPlan,
    FeasibleInsertion,
    RepositionAssignment,
)


@dataclass(slots=True)
class _ResidualEdge:
    to: int
    reverse: int
    capacity: int
    cost: int
    candidate_index: int | None = None


def _add_edge(
    graph: list[list[_ResidualEdge]],
    source: int,
    target: int,
    capacity: int,
    cost: int,
    *,
    candidate_index: int | None = None,
) -> None:
    forward = _ResidualEdge(
        to=target,
        reverse=len(graph[target]),
        capacity=capacity,
        cost=cost,
        candidate_index=candidate_index,
    )
    reverse = _ResidualEdge(
        to=source,
        reverse=len(graph[source]),
        capacity=0,
        cost=-cost,
    )
    graph[source].append(forward)
    graph[target].append(reverse)


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


def maximum_margin_matching(
    candidates: tuple[FeasibleInsertion, ...],
) -> tuple[FeasibleInsertion, ...]:
    """Maximum-weight bipartite matching using deterministic min-cost flow.

    Each structural empty-leg window can accept at most one future mission and each mission can be
    assigned at most once. Only positive-margin candidates should be supplied.
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

    source = 0
    left_offset = 1
    right_offset = left_offset + len(left_keys)
    sink = right_offset + len(right_keys)
    graph: list[list[_ResidualEdge]] = [[] for _ in range(sink + 1)]

    for index in range(len(left_keys)):
        _add_edge(graph, source, left_offset + index, 1, 0)
    for index in range(len(right_keys)):
        _add_edge(graph, right_offset + index, sink, 1, 0)

    max_assignments = min(len(left_keys), len(right_keys))
    # One minor unit of margin must dominate the maximum possible aggregate tie penalty.
    tie_scale = max_assignments * len(ordered) + 1
    for candidate_index, item in enumerate(ordered):
        margin_minor = item.margin.amount_minor
        if margin_minor <= 0:
            continue
        cost = -(margin_minor * tie_scale) + candidate_index
        _add_edge(
            graph,
            left_offset + left_index[_left_key(item)],
            right_offset + right_index[item.opportunity.mission_id.value],
            1,
            cost,
            candidate_index=candidate_index,
        )

    node_count = len(graph)
    while True:
        distance: list[int | None] = [None] * node_count
        previous_node = [-1] * node_count
        previous_edge = [-1] * node_count
        distance[source] = 0

        for _ in range(node_count - 1):
            changed = False
            for node, edges in enumerate(graph):
                base = distance[node]
                if base is None:
                    continue
                for edge_index, edge in enumerate(edges):
                    if edge.capacity <= 0:
                        continue
                    proposal = base + edge.cost
                    current = distance[edge.to]
                    if current is None or proposal < current:
                        distance[edge.to] = proposal
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index
                        changed = True
            if not changed:
                break

        sink_distance = distance[sink]
        if sink_distance is None or sink_distance >= 0:
            break

        node = sink
        while node != source:
            parent = previous_node[node]
            edge_index = previous_edge[node]
            if parent < 0 or edge_index < 0:
                raise RuntimeError("min-cost flow predecessor chain is incomplete")
            edge = graph[parent][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = parent

    selected_indexes: set[int] = set()
    for left_node in range(left_offset, right_offset):
        for edge in graph[left_node]:
            if edge.candidate_index is not None and edge.capacity == 0:
                selected_indexes.add(edge.candidate_index)

    return tuple(ordered[index] for index in sorted(selected_indexes))


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
        previous_revenue_distance_tenths_nm=(
            item.empty_leg.previous_revenue_distance_tenths_nm
        ),
        previous_revenue_minutes=item.empty_leg.previous_revenue_minutes,
        baseline_reposition_distance_tenths_nm=item.empty_leg.baseline_distance_tenths_nm,
        pre_reposition_distance_tenths_nm=item.pre_reposition_distance_tenths_nm,
        revenue_distance_tenths_nm=item.revenue_distance_tenths_nm,
        post_reposition_distance_tenths_nm=item.post_reposition_distance_tenths_nm,
        baseline_reposition_cost=item.empty_leg.baseline_reposition_cost,
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
