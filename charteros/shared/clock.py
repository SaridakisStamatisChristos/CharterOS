from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from charteros.domain.shared.exceptions import DomainValidationError


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@runtime_checkable
class Clock(Protocol):
    """Authoritative application time source."""

    def now(self) -> datetime:
        """Return an aware UTC timestamp."""


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock backed by the system's UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """Deterministic test clock fixed at one explicit instant."""

    instant: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "instant", _utc(self.instant, field_name="instant"))

    def now(self) -> datetime:
        return self.instant
