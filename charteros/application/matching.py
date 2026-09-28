from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import AirportRepository
from charteros.application.ports.matching import MatchingSnapshotRepository
from charteros.application.ports.missions import MissionRepository
from charteros.domain.missions import MissionId, MissionStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.matching import (
    POLICY_VERSION,
    REFERENCE_CURRENCY,
    MatchingDecision,
    evaluate_candidate,
    haversine_distance_tenths_nm,
    rank_matches,
    rejection_summary,
    required_range_nm,
)

MAX_CANDIDATES = 2_000


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("known_as_of must be timezone-aware")
    return value.astimezone(UTC)


class MatchingService:
    def __init__(
        self,
        *,
        missions: MissionRepository,
        airports: AirportRepository,
        snapshots: MatchingSnapshotRepository,
    ) -> None:
        self._missions = missions
        self._airports = airports
        self._snapshots = snapshots

    def match_mission(
        self,
        *,
        mission_id: MissionId,
        known_as_of: datetime,
    ) -> MatchingDecision:
        mission = self._missions.get(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")
        if mission.status is not MissionStatus.OPEN:
            raise EntityConflictError("only open missions can be matched")

        origin = self._airports.get(mission.origin_airport_id)
        destination = self._airports.get(mission.destination_airport_id)
        if origin is None or destination is None:
            raise EntityNotFoundError("mission airport dependency does not exist")

        knowledge_cutoff = _utc(known_as_of)
        position_event_cutoff = min(knowledge_cutoff, mission.departure_window.start)
        route_distance = haversine_distance_tenths_nm(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        candidates = self._snapshots.load_candidates(
            known_as_of=knowledge_cutoff,
            position_event_cutoff=position_event_cutoff,
            availability_from=mission.departure_window.start,
            availability_to=mission.departure_window.end,
            limit=MAX_CANDIDATES,
        )
        evaluations = tuple(
            evaluate_candidate(
                mission=mission,
                candidate=candidate,
                origin_latitude=origin.latitude,
                origin_longitude=origin.longitude,
                route_distance_tenths_nm=route_distance,
                decision_time=position_event_cutoff,
            )
            for candidate in candidates
        )
        drafts = tuple(
            evaluation.draft
            for evaluation in evaluations
            if evaluation.draft is not None
        )
        matches = rank_matches(drafts)
        return MatchingDecision(
            policy_version=POLICY_VERSION,
            reference_currency=REFERENCE_CURRENCY,
            known_as_of=knowledge_cutoff,
            position_event_cutoff=position_event_cutoff,
            route_distance_tenths_nm=route_distance,
            required_range_nm=required_range_nm(route_distance),
            candidate_count=len(candidates),
            feasible_count=len(matches),
            rejection_summary=rejection_summary(evaluations),
            matches=matches,
        )
