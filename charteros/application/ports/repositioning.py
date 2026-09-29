from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.aircraft import AircraftId
from charteros.domain.operators import OperatorId
from charteros.repositioning import QuotedFutureLeg


class RepositionOpportunityRepository(Protocol):
    def list_quoted_future_legs(
        self,
        *,
        aircraft_ids: tuple[AircraftId, ...],
        operator_ids: tuple[OperatorId, ...],
        window_start: datetime,
        window_end: datetime,
        evaluated_at: datetime,
        limit: int,
    ) -> tuple[QuotedFutureLeg, ...]: ...
