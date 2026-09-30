from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.aircraft import AircraftId
from charteros.matching import MatchingCandidateSnapshot


class MatchingSnapshotRepository(Protocol):
    def lock_aircraft_and_operator(self, aircraft_id: AircraftId) -> None: ...

    def load_candidates(
        self,
        *,
        known_as_of: datetime,
        position_event_cutoff: datetime,
        availability_from: datetime,
        availability_to: datetime,
        limit: int,
    ) -> tuple[MatchingCandidateSnapshot, ...]: ...

    def load_candidates_by_aircraft_ids(
        self,
        *,
        aircraft_ids: tuple[AircraftId, ...],
        known_as_of: datetime,
        position_event_cutoff: datetime,
        availability_from: datetime,
        availability_to: datetime,
    ) -> tuple[MatchingCandidateSnapshot, ...]: ...
