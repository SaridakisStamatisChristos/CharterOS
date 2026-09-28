from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4

from charteros.domain.shared.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True)
class TypedId:
    """Immutable UUID-backed identifier with runtime type separation via subclasses."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise DomainValidationError("typed IDs require a UUID value")

    @classmethod
    def new(cls) -> Self:
        return cls(uuid4())

    @classmethod
    def parse(cls, value: str) -> Self:
        try:
            parsed = UUID(value)
        except (AttributeError, ValueError) as exc:
            raise DomainValidationError(f"invalid UUID for {cls.__name__}") from exc
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


class EventId(TypedId):
    __slots__ = ()


class CorrelationId(TypedId):
    __slots__ = ()
