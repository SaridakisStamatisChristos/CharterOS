from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from charteros.domain.shared.exceptions import DomainValidationError


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Canonical UTC half-open interval [start, end)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _as_utc(self.start, field_name="start")
        end = _as_utc(self.end, field_name="end")
        if end <= start:
            raise DomainValidationError("time range end must be after start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, instant: datetime) -> bool:
        point = _as_utc(instant, field_name="instant")
        return self.start <= point < self.end

    def overlaps(self, other: TimeRange) -> bool:
        return self.start < other.end and other.start < self.end
