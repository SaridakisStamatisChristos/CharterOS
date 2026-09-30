from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from charteros.domain.airports import AirportId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId

if TYPE_CHECKING:
    from charteros.domain.aircraft.timeline import (
        AircraftAvailabilityRecord,
        AircraftPositionObservation,
    )

_REGISTRATION = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}$")


class AircraftId(TypedId):
    __slots__ = ()


class AircraftTypeId(TypedId):
    __slots__ = ()


class AircraftStatus(StrEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    GROUNDED = "grounded"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class AircraftType:
    id: AircraftTypeId
    manufacturer: str
    model: str
    category: str
    seats_min: int
    seats_max: int
    range_nm: int
    runway_requirements: dict[str, object]
    baggage_cargo_profile: dict[str, object]

    def __post_init__(self) -> None:
        manufacturer = " ".join(self.manufacturer.split())
        model = " ".join(self.model.split())
        category = " ".join(self.category.split()).lower()
        if not 1 <= len(manufacturer) <= 100:
            raise DomainValidationError("manufacturer must contain 1 to 100 characters")
        if not 1 <= len(model) <= 100:
            raise DomainValidationError("model must contain 1 to 100 characters")
        if not 1 <= len(category) <= 50:
            raise DomainValidationError("category must contain 1 to 50 characters")
        if self.seats_min < 0 or self.seats_max < self.seats_min:
            raise DomainValidationError("aircraft type seat bounds are invalid")
        if self.range_nm <= 0:
            raise DomainValidationError("aircraft type range_nm must be positive")
        object.__setattr__(self, "manufacturer", manufacturer)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "runway_requirements", dict(self.runway_requirements))
        object.__setattr__(self, "baggage_cargo_profile", dict(self.baggage_cargo_profile))

    @classmethod
    def create(
        cls,
        *,
        manufacturer: str,
        model: str,
        category: str,
        seats_min: int,
        seats_max: int,
        range_nm: int,
        runway_requirements: dict[str, object] | None = None,
        baggage_cargo_profile: dict[str, object] | None = None,
    ) -> AircraftType:
        return cls(
            id=AircraftTypeId.new(),
            manufacturer=manufacturer,
            model=model,
            category=category,
            seats_min=seats_min,
            seats_max=seats_max,
            range_nm=range_nm,
            runway_requirements=runway_requirements or {},
            baggage_cargo_profile=baggage_cargo_profile or {},
        )


class Aircraft(AggregateRoot[AircraftId]):
    aggregate_type = "aircraft"

    def __init__(
        self,
        aircraft_id: AircraftId,
        *,
        operator_id: OperatorId,
        registration: str,
        aircraft_type_id: AircraftTypeId,
        seat_capacity: int,
        cargo_capacity: Decimal,
        range_nm: int,
        home_base_id: AirportId,
        status: AircraftStatus,
        version: int = 0,
    ) -> None:
        super().__init__(aircraft_id, version=version)
        normalized_registration = registration.strip().upper()
        if not _REGISTRATION.fullmatch(normalized_registration):
            raise DomainValidationError(
                "registration must be 2 to 16 uppercase alphanumeric/hyphen characters"
            )
        if seat_capacity <= 0:
            raise DomainValidationError("seat_capacity must be positive")
        if cargo_capacity < 0:
            raise DomainValidationError("cargo_capacity cannot be negative")
        if range_nm <= 0:
            raise DomainValidationError("range_nm must be positive")
        self.operator_id = operator_id
        self.registration = normalized_registration
        self.aircraft_type_id = aircraft_type_id
        self.seat_capacity = seat_capacity
        self.cargo_capacity = cargo_capacity
        self.range_nm = range_nm
        self.home_base_id = home_base_id
        self.status = AircraftStatus(status)

    @classmethod
    def create(
        cls,
        *,
        operator_id: OperatorId,
        registration: str,
        aircraft_type: AircraftType,
        seat_capacity: int,
        cargo_capacity: Decimal,
        range_nm: int,
        home_base_id: AirportId,
        recorded_at: datetime,
        status: AircraftStatus = AircraftStatus.ACTIVE,
        correlation_id: CorrelationId | None = None,
    ) -> Aircraft:
        if not aircraft_type.seats_min <= seat_capacity <= aircraft_type.seats_max:
            raise DomainValidationError("seat_capacity must be within aircraft type seat bounds")
        if range_nm > aircraft_type.range_nm:
            raise DomainValidationError("range_nm cannot exceed aircraft type reference range")
        aircraft = cls(
            AircraftId.new(),
            operator_id=operator_id,
            registration=registration,
            aircraft_type_id=aircraft_type.id,
            seat_capacity=seat_capacity,
            cargo_capacity=cargo_capacity,
            range_nm=range_nm,
            home_base_id=home_base_id,
            status=status,
        )
        aircraft._record_event(
            "AIRCRAFT_REGISTERED",
            {
                "operator_id": str(aircraft.operator_id),
                "registration": aircraft.registration,
                "aircraft_type_id": str(aircraft.aircraft_type_id),
                "home_base_id": str(aircraft.home_base_id),
                "status": aircraft.status.value,
            },
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        return aircraft

    def record_position_observation(
        self,
        observation: AircraftPositionObservation,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if observation.aircraft_id != self.id:
            raise DomainValidationError("position observation belongs to a different aircraft")
        self._record_event(
            "AIRCRAFT_POSITION_RECORDED",
            observation.event_payload(),
            correlation_id=correlation_id,
            recorded_at=observation.recorded_at,
            occurred_at=observation.recorded_at,
        )

    def record_availability_change(
        self,
        record: AircraftAvailabilityRecord,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if record.aircraft_id != self.id:
            raise DomainValidationError("availability record belongs to a different aircraft")
        self._record_event(
            "AIRCRAFT_AVAILABILITY_CHANGED",
            record.event_payload(),
            correlation_id=correlation_id,
            recorded_at=record.recorded_at,
            occurred_at=record.recorded_at,
        )
