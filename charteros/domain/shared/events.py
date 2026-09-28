from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId


def _canonical_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError("event payload floats must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainValidationError("event payload object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    raise DomainValidationError(f"unsupported event payload value type: {type(value).__name__}")


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Immutable, canonically serializable domain-event envelope."""

    event_id: EventId
    aggregate_type: str
    aggregate_id: TypedId
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_id: TypedId | None = None
    correlation_id: CorrelationId | None = None
    causation_id: EventId | None = None
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise DomainValidationError("event_id must be an EventId")
        if not isinstance(self.aggregate_id, TypedId):
            raise DomainValidationError("aggregate_id must be a TypedId")
        if not self.aggregate_type or self.aggregate_type.strip() != self.aggregate_type:
            raise DomainValidationError("aggregate_type must be a non-empty canonical string")
        if not self.event_type or self.event_type.strip() != self.event_type:
            raise DomainValidationError("event_type must be a non-empty canonical string")
        if (
            not isinstance(self.aggregate_version, int)
            or isinstance(self.aggregate_version, bool)
            or self.aggregate_version < 1
        ):
            raise DomainValidationError("aggregate_version must be a positive integer")
        if (
            not isinstance(self.event_version, int)
            or isinstance(self.event_version, bool)
            or self.event_version < 1
        ):
            raise DomainValidationError("event_version must be a positive integer")

        occurred_at = _canonical_datetime(self.occurred_at, field_name="occurred_at")
        recorded_at = _canonical_datetime(self.recorded_at, field_name="recorded_at")
        if recorded_at < occurred_at:
            raise DomainValidationError("recorded_at cannot precede occurred_at")
        if self.actor_id is not None and not isinstance(self.actor_id, TypedId):
            raise DomainValidationError("actor_id must be a TypedId when present")
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise DomainValidationError("correlation_id must be a CorrelationId when present")
        if self.causation_id is not None and not isinstance(self.causation_id, EventId):
            raise DomainValidationError("causation_id must be an EventId when present")
        if not isinstance(self.payload, Mapping):
            raise DomainValidationError("event payload must be a mapping")

        frozen_payload = _freeze_json(self.payload)
        if not isinstance(frozen_payload, Mapping):
            raise DomainValidationError("event payload must be a JSON object")
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "payload", frozen_payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "aggregate_version": self.aggregate_version,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "occurred_at": _format_datetime(self.occurred_at),
            "recorded_at": _format_datetime(self.recorded_at),
            "actor_id": str(self.actor_id) if self.actor_id is not None else None,
            "correlation_id": (
                str(self.correlation_id) if self.correlation_id is not None else None
            ),
            "causation_id": str(self.causation_id) if self.causation_id is not None else None,
            "payload": _thaw_json(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
