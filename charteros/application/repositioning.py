from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from charteros.application.exceptions import EntityConflictError
from charteros.application.graph_queries import (
    MAX_EMPTY_LEG_WINDOW,
    BookingFlightLineage,
    EmptyLegCandidate,
    GraphQueryService,
)
from charteros.application.ports.catalog import AirportRepository
from charteros.application.ports.matching import MatchingSnapshotRepository
from charteros.application.ports.repositioning import RepositionOpportunityRepository
from charteros.domain.aircraft import AircraftId
from charteros.domain.airports import Airport, AirportId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.repositioning import (
    POLICY_VERSION,
    BaselineEmptyLeg,
    BaselineEvaluation,
    FeasibleInsertion,
    InsertionEvaluation,
    QuotedFutureLeg,
    RepositionOptimization,
    StructuralEmptyLeg,
    build_currency_plan,
    evaluate_baseline,
    evaluate_insertion,
    merge_rejection_counts,
)

MAX_STRUCTURAL_EMPTY_LEGS = 100
MAX_QUOTED_FUTURE_LEGS = 2_000


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_structural_lineage(
    item: EmptyLegCandidate,
    previous: BookingFlightLineage,
    following: BookingFlightLineage,
) -> None:
    if (
        previous.booking_id != item.previous_booking_id
        or previous.mission_id != item.previous_mission_id
        or previous.aircraft_id != item.aircraft_id
        or previous.operator_id != item.operator_id
        or previous.destination_airport_id != item.from_airport_id
        or previous.departure_to != item.window_start
    ):
        raise EntityConflictError(
            "PR16 empty-leg candidate conflicts with previous booking lineage"
        )
    if (
        following.booking_id != item.next_booking_id
        or following.mission_id != item.next_mission_id
        or following.aircraft_id != item.aircraft_id
        or following.operator_id != item.operator_id
        or following.origin_airport_id != item.to_airport_id
        or following.departure_from != item.window_end
    ):
        raise EntityConflictError("PR16 empty-leg candidate conflicts with next booking lineage")


class RepositioningService:
    def __init__(
        self,
        *,
        graph: GraphQueryService,
        airports: AirportRepository,
        snapshots: MatchingSnapshotRepository,
        opportunities: RepositionOpportunityRepository,
    ) -> None:
        self._graph = graph
        self._airports = airports
        self._snapshots = snapshots
        self._opportunities = opportunities

    def optimize(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        evaluated_at: datetime,
        empty_leg_limit: int = MAX_STRUCTURAL_EMPTY_LEGS,
        opportunity_limit: int = MAX_QUOTED_FUTURE_LEGS,
    ) -> RepositionOptimization:
        start = _utc(window_start, field_name="window_start")
        end = _utc(window_end, field_name="window_end")
        evaluated = _utc(evaluated_at, field_name="evaluated_at")
        if end <= start:
            raise DomainValidationError("window_end must be after window_start")
        if end - start > MAX_EMPTY_LEG_WINDOW:
            raise DomainValidationError("reposition optimization window cannot exceed 90 days")
        if not 1 <= empty_leg_limit <= MAX_STRUCTURAL_EMPTY_LEGS:
            raise DomainValidationError("empty_leg_limit must be between 1 and 100")
        if not 1 <= opportunity_limit <= MAX_QUOTED_FUTURE_LEGS:
            raise DomainValidationError("opportunity_limit must be between 1 and 2000")

        graph_items = self._graph.empty_leg_candidates(
            window_start=start,
            window_end=end,
            limit=empty_leg_limit,
        )
        projection_version = self._graph.projection_version
        graph_knowledge_cutoff = self._graph.knowledge_cutoff
        if evaluated < graph_knowledge_cutoff:
            raise EntityConflictError(
                "evaluated_at predates the active Charter Graph knowledge cutoff; "
                "historical PR18 replay requires an as-of graph projection"
            )
        structural_items: list[StructuralEmptyLeg] = []
        for graph_item in graph_items:
            previous = self._graph.booking_flight_lineage(
                booking_id=graph_item.previous_booking_id
            )
            following = self._graph.booking_flight_lineage(
                booking_id=graph_item.next_booking_id
            )
            _validate_structural_lineage(graph_item, previous, following)
            structural_items.append(
                StructuralEmptyLeg(
                    aircraft_id=graph_item.aircraft_id,
                    operator_id=graph_item.operator_id,
                    previous_booking_id=graph_item.previous_booking_id,
                    previous_mission_id=graph_item.previous_mission_id,
                    next_booking_id=graph_item.next_booking_id,
                    next_mission_id=graph_item.next_mission_id,
                    previous_origin_airport_id=previous.origin_airport_id,
                    from_airport_id=graph_item.from_airport_id,
                    from_icao=graph_item.from_icao,
                    to_airport_id=graph_item.to_airport_id,
                    to_icao=graph_item.to_icao,
                    window_start=graph_item.window_start,
                    window_end=graph_item.window_end,
                    gap_minutes=graph_item.gap_minutes,
                    evidence_kind=graph_item.evidence_kind,
                )
            )
        structural = tuple(structural_items)
        if not structural:
            return RepositionOptimization(
                policy_version=POLICY_VERSION,
                projection_version=projection_version,
                graph_knowledge_cutoff=graph_knowledge_cutoff,
                evaluated_at=evaluated,
                window_start=start,
                window_end=end,
                structural_empty_leg_count=0,
                evaluable_empty_leg_count=0,
                direct_reposition_feasible_count=0,
                quoted_future_leg_count=0,
                feasible_candidate_count=0,
                rejection_summary={},
                global_plan_available=True,
                currency_plans=(),
            )

        aircraft_ids = tuple(
            AircraftId(value)
            for value in sorted(
                {item.aircraft_id for item in structural},
                key=lambda value: value.hex,
            )
        )
        operator_ids = tuple(
            OperatorId(value)
            for value in sorted(
                {item.operator_id for item in structural},
                key=lambda value: value.hex,
            )
        )
        snapshots = self._snapshots.load_candidates_by_aircraft_ids(
            aircraft_ids=aircraft_ids,
            known_as_of=evaluated,
            position_event_cutoff=evaluated,
            availability_from=start,
            availability_to=end,
        )
        snapshot_by_aircraft = {item.aircraft_id.value: item for item in snapshots}
        for structural_item in structural:
            snapshot = snapshot_by_aircraft.get(structural_item.aircraft_id)
            if snapshot is None:
                raise EntityConflictError(
                    "Charter Graph empty-leg aircraft is missing from canonical fleet state"
                )
            if snapshot.operator_id.value != structural_item.operator_id:
                raise EntityConflictError(
                    "Charter Graph empty-leg operator conflicts with canonical aircraft ownership"
                )

        opportunities = self._opportunities.list_quoted_future_legs(
            aircraft_ids=aircraft_ids,
            operator_ids=operator_ids,
            window_start=start,
            window_end=end,
            evaluated_at=evaluated,
            limit=opportunity_limit,
        )

        airport_ids: set[AirportId] = (
            {AirportId(item.previous_origin_airport_id) for item in structural}
            | {AirportId(item.from_airport_id) for item in structural}
            | {AirportId(item.to_airport_id) for item in structural}
        )
        for item in opportunities:
            airport_ids.add(item.origin_airport_id)
            airport_ids.add(item.destination_airport_id)
        airports = self._load_airports(airport_ids)

        evaluations: list[BaselineEvaluation | InsertionEvaluation] = []
        baselines: list[tuple[StructuralEmptyLeg, BaselineEmptyLeg]] = []
        for structural_item in structural:
            snapshot = snapshot_by_aircraft[structural_item.aircraft_id]
            baseline_eval = evaluate_baseline(
                structural=structural_item,
                candidate=snapshot,
                previous_origin_airport=airports[
                    AirportId(structural_item.previous_origin_airport_id)
                ],
                from_airport=airports[AirportId(structural_item.from_airport_id)],
                continuity_airport=airports[AirportId(structural_item.to_airport_id)],
            )
            evaluations.append(baseline_eval)
            if baseline_eval.baseline is not None:
                baselines.append((structural_item, baseline_eval.baseline))

        opportunities_by_aircraft: dict[tuple[UUID, UUID], list[QuotedFutureLeg]] = defaultdict(
            list
        )
        for opportunity in opportunities:
            opportunities_by_aircraft[
                (opportunity.aircraft_id.value, opportunity.operator_id.value)
            ].append(opportunity)

        feasible: list[FeasibleInsertion] = []
        for structural_item, baseline in baselines:
            snapshot = snapshot_by_aircraft[structural_item.aircraft_id]
            for opportunity in opportunities_by_aircraft.get(
                (structural_item.aircraft_id, structural_item.operator_id),
                [],
            ):
                insertion_eval = evaluate_insertion(
                    baseline=baseline,
                    candidate=snapshot,
                    opportunity=opportunity,
                    from_airport=airports[AirportId(structural_item.from_airport_id)],
                    mission_origin=airports[opportunity.origin_airport_id],
                    mission_destination=airports[opportunity.destination_airport_id],
                    continuity_airport=airports[AirportId(structural_item.to_airport_id)],
                )
                evaluations.append(insertion_eval)
                if insertion_eval.insertion is not None:
                    feasible.append(insertion_eval.insertion)

        grouped: dict[str, list[FeasibleInsertion]] = defaultdict(list)
        for insertion in feasible:
            grouped[str(insertion.currency)].append(insertion)
        plans = tuple(build_currency_plan(tuple(grouped[currency])) for currency in sorted(grouped))

        return RepositionOptimization(
            policy_version=POLICY_VERSION,
            projection_version=projection_version,
            graph_knowledge_cutoff=graph_knowledge_cutoff,
            evaluated_at=evaluated,
            window_start=start,
            window_end=end,
            structural_empty_leg_count=len(structural),
            evaluable_empty_leg_count=len(baselines),
            direct_reposition_feasible_count=sum(
                1 for _, baseline in baselines if baseline.baseline_reposition_feasible
            ),
            quoted_future_leg_count=len(opportunities),
            feasible_candidate_count=len(feasible),
            rejection_summary=merge_rejection_counts(tuple(evaluations)),
            global_plan_available=len(plans) <= 1,
            currency_plans=plans,
        )

    def _load_airports(self, airport_ids: set[AirportId]) -> dict[AirportId, Airport]:
        result: dict[AirportId, Airport] = {}
        for airport_id in sorted(airport_ids, key=lambda value: value.value.hex):
            airport = self._airports.get(airport_id)
            if airport is None:
                raise EntityConflictError(
                    "reposition optimization references a missing canonical airport"
                )
            result[airport_id] = airport
        return result
