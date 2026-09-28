from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.matching import MatchingCandidateSnapshot


class MatchingSnapshotRepository(Protocol):
    def load_candidates(
        self,
        *,
        known_as_of: datetime,
        position_event_cutoff: datetime,
        availability_from: datetime,
        availability_to: datetime,
        limit: int,
    ) -> tuple[MatchingCandidateSnapshot, ...]: ...
