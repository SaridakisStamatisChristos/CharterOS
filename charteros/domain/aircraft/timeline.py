from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from charteros.domain.airports import AirportId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import TypedId
from charteros.domain.shared.time_range import TimeRange

from .model import AircraftId


class PositionObservationId(TypedId):
    __slots__ = ()


class AvailabilityRecordId(TypedId):
    __slots__ = ()


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


def ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _source(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 64:
        raise DomainValidationError("source must contain 1 to 64 characters")
    return normalized


def _provenance(value: Mapping[str, object] | None) -> Mapping[str, object]:
    if value is None:
        return MappingProxyType({})
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise DomainValidationError("provenance keys must be strings")
        copied[key] = item
    return MappingProxyType(copied)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AircraftPositionObservation:
    id: PositionObservationId
    aircraft_id: AircraftId
    airport_id: AirportId | None
    latitude: Decimal | None
    longitude: Decimal | None
    event_time: datetime
    recorded_at: datetime
    source: str
    provenance: Mapping[str, object]
    created_at: datetime

    def __post_init__(self) -> None:
        event_time = ensure_utc(self.event_time, field_name="event_time")
        recorded_at = ensure_utc(self.recorded_at, field_name="recorded_at")
        created_at = ensure_utc(self.created_at, field_name="created_at")
        if recorded_at < event_time:
            raise DomainValidationError("position recorded_at cannot precede event_time")

        has_airport = self.airport_id is not None
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_airport == (has_latitude or has_longitude):
            raise DomainValidationError(
                "position must use either airport_id or an explicit latitude/longitude pair"
            )
        if has_latitude != has_longitude:
            raise DomainValidationError("position coordinates require both latitude and longitude")
        if self.latitude is not None and not Decimal("-90") <= self.latitude <= Decimal("90"):
            raise DomainValidationError("latitude must be between -90 and 90")
        if self.longitude is not None and not Decimal("-180") <= self.longitude <= Decimal("180"):
            raise DomainValidationError("longitude must be between -180 and 180")

        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "source", _source(self.source))
        object.__setattr__(self, "provenance", _provenance(self.provenance))

    @classmethod
    def create(
        cls,
        *,
        aircraft_id: AircraftId,
        airport_id: AirportId | None,
        latitude: Decimal | None,
        longitude: Decimal | None,
        event_time: datetime,
        recorded_at: datetime,
        source: str,
        provenance: Mapping[str, object] | None = None,
    ) -> AircraftPositionObservation:
        return cls(
            id=PositionObservationId.new(),
            aircraft_id=aircraft_id,
            airport_id=airport_id,
            latitude=latitude,
            longitude=longitude,
            event_time=event_time,
            recorded_at=recorded_at,
            source=source,
            provenance=_provenance(provenance),
            created_at=recorded_at,
        )

    def event_payload(self) -> dict[str, object]:
        return {
            "position_id": str(self.id),
            "airport_id": str(self.airport_id) if self.airport_id is not None else None,
            "latitude": str(self.latitude) if self.latitude is not None else None,
            "longitude": str(self.longitude) if self.longitude is not None else None,
            "event_time": _iso(self.event_time),
            "knowledge_time": _iso(self.recorded_at),
            "source": self.source,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class AircraftAvailabilityRecord:
    id: AvailabilityRecordId
    aircraft_id: AircraftId
    interval: TimeRange
    status: AvailabilityStatus
    recorded_at: datetime
    source: str
    reason: str | None
    provenance: Mapping[str, object]
    supersedes_id: AvailabilityRecordId | None
    superseded_at: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        recorded_at = ensure_utc(self.recorded_at, field_name="recorded_at")
        created_at = ensure_utc(self.created_at, field_name="created_at")
        superseded_at = (
            ensure_utc(self.superseded_at, field_name="superseded_at")
            if self.superseded_at is not None
            else None
        )
        if superseded_at is not None and superseded_at < recorded_at:
            raise DomainValidationError("superseded_at cannot precede recorded_at")
        if self.supersedes_id is not None and self.supersedes_id == self.id:
            raise DomainValidationError("an availability record cannot supersede itself")
        reason = self.reason.strip() if self.reason is not None else None
        if reason == "":
            reason = None
        if reason is not None and len(reason) > 500:
            raise DomainValidationError("reason must contain at most 500 characters")

        object.__setattr__(self, "status", AvailabilityStatus(self.status))
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "superseded_at", superseded_at)
        object.__setattr__(self, "source", _source(self.source))
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "provenance", _provenance(self.provenance))

    @classmethod
    def create(
        cls,
        *,
        aircraft_id: AircraftId,
        valid_from: datetime,
        valid_to: datetime,
        status: AvailabilityStatus,
        recorded_at: datetime,
        source: str,
        reason: str | None = None,
        provenance: Mapping[str, object] | None = None,
        supersedes_id: AvailabilityRecordId | None = None,
    ) -> AircraftAvailabilityRecord:
        return cls(
            id=AvailabilityRecordId.new(),
            aircraft_id=aircraft_id,
            interval=TimeRange(valid_from, valid_to),
            status=status,
            recorded_at=recorded_at,
            source=source,
            reason=reason,
            provenance=_provenance(provenance),
            supersedes_id=supersedes_id,
            superseded_at=None,
            created_at=recorded_at,
        )

    def contains(self, event_time: datetime) -> bool:
        return self.interval.contains(event_time)

    def is_authoritative_as_of(self, known_as_of: datetime) -> bool:
        knowledge_time = ensure_utc(known_as_of, field_name="known_as_of")
        return self.recorded_at <= knowledge_time and (
            self.superseded_at is None or knowledge_time < self.superseded_at
        )

    def event_payload(self) -> dict[str, object]:
        return {
            "availability_id": str(self.id),
            "valid_from": _iso(self.interval.start),
            "valid_to": _iso(self.interval.end),
            "status": self.status.value,
            "knowledge_time": _iso(self.recorded_at),
            "source": self.source,
            "reason": self.reason,
            "provenance": dict(self.provenance),
            "supersedes_id": (
                str(self.supersedes_id) if self.supersedes_id is not None else None
            ),
        }


def select_position_as_of(
    observations: tuple[AircraftPositionObservation, ...],
    *,
    event_time: datetime,
    known_as_of: datetime,
) -> AircraftPositionObservation | None:
    event_cutoff = ensure_utc(event_time, field_name="event_time")
    knowledge_cutoff = ensure_utc(known_as_of, field_name="known_as_of")
    visible = (
        observation
        for observation in observations
        if observation.event_time <= event_cutoff and observation.recorded_at <= knowledge_cutoff
    )
    return max(
        visible,
        key=lambda item: (item.event_time, item.recorded_at, item.id.value.hex),
        default=None,
    )


def select_availability_as_of(
    records: tuple[AircraftAvailabilityRecord, ...],
    *,
    event_time: datetime,
    known_as_of: datetime,
) -> AircraftAvailabilityRecord | None:
    point = ensure_utc(event_time, field_name="event_time")
    knowledge_cutoff = ensure_utc(known_as_of, field_name="known_as_of")
    visible = (
        record
        for record in records
        if record.contains(point) and record.is_authoritative_as_of(knowledge_cutoff)
    )
    return max(
        visible,
        key=lambda item: (item.recorded_at, item.id.value.hex),
        default=None,
    )
