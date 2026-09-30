from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import AirportRepository
from charteros.application.ports.matching import MatchingSnapshotRepository
from charteros.domain.aircraft import AircraftId
from charteros.domain.missions import Mission
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import (
    POLICY_VERSION,
    CandidateEvaluation,
    MatchingCandidateSnapshot,
    evaluate_candidate,
    haversine_distance_tenths_nm,
    required_range_nm,
)


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AircraftMissionFeasibility:
    policy_version: str
    known_as_of: datetime
    position_event_cutoff: datetime
    departure_window: TimeRange
    route_distance_tenths_nm: int
    required_range_nm: int
    candidate: MatchingCandidateSnapshot
    evaluation: CandidateEvaluation

    @property
    def feasible(self) -> bool:
        return self.evaluation.draft is not None


class AircraftMissionFeasibilityService:
    """Single-aircraft view of the canonical matching-v1 feasibility policy."""

    def __init__(
        self,
        *,
        airports: AirportRepository,
        snapshots: MatchingSnapshotRepository,
    ) -> None:
        self._airports = airports
        self._snapshots = snapshots

    def evaluate(
        self,
        *,
        mission: Mission,
        aircraft_id: AircraftId,
        known_as_of: datetime,
        departure_window: TimeRange | None = None,
    ) -> AircraftMissionFeasibility:
        decision_time = _utc(known_as_of, field_name="known_as_of")
        window = departure_window or mission.departure_window
        position_event_cutoff = min(decision_time, window.start)

        origin = self._airports.get(mission.origin_airport_id)
        destination = self._airports.get(mission.destination_airport_id)
        if origin is None or destination is None:
            raise EntityNotFoundError("mission airport dependency does not exist")

        route_distance = haversine_distance_tenths_nm(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        candidates = self._snapshots.load_candidates_by_aircraft_ids(
            aircraft_ids=(aircraft_id,),
            known_as_of=decision_time,
            position_event_cutoff=position_event_cutoff,
            availability_from=window.start,
            availability_to=window.end,
        )
        if len(candidates) != 1:
            raise EntityConflictError(
                "aircraft feasibility snapshot is unavailable for the requested decision time"
            )
        candidate = candidates[0]
        if candidate.aircraft_id != aircraft_id:
            raise EntityConflictError("aircraft feasibility snapshot identity is inconsistent")

        evaluation = evaluate_candidate(
            mission=mission,
            candidate=candidate,
            origin_latitude=origin.latitude,
            origin_longitude=origin.longitude,
            route_distance_tenths_nm=route_distance,
            decision_time=decision_time,
            departure_window=window,
        )
        return AircraftMissionFeasibility(
            policy_version=POLICY_VERSION,
            known_as_of=decision_time,
            position_event_cutoff=position_event_cutoff,
            departure_window=window,
            route_distance_tenths_nm=route_distance,
            required_range_nm=required_range_nm(route_distance),
            candidate=candidate,
            evaluation=evaluation,
        )
