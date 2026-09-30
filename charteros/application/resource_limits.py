from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from charteros.domain.shared.exceptions import DomainValidationError


class AbuseBudget(StrEnum):
    MATCHING = "matching"
    REPOSITIONING = "repositioning"
    EVIDENCE = "evidence"
    GRAPH = "graph"


@dataclass(frozen=True, slots=True)
class RateBudgetDecision:
    allowed: bool
    request_count: int
    limit: int
    retry_after_seconds: int


MAX_TIMELINE_WINDOW = timedelta(days=366)
MAX_CALENDAR_WINDOW = timedelta(days=366)
MAX_OPTIMIZATION_WINDOW = timedelta(days=31)


def validate_bounded_window(
    *,
    start: datetime,
    end: datetime,
    maximum: timedelta,
    name: str,
) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise DomainValidationError(f"{name} start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise DomainValidationError(f"{name} end must be timezone-aware")
    if end <= start:
        raise DomainValidationError(f"{name} end must be after start")
    if end - start > maximum:
        raise DomainValidationError(
            f"{name} exceeds maximum duration of {maximum.days} days"
        )
