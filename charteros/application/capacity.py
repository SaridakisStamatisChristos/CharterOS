from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.capacity import CapacityReferenceRepository
from charteros.application.ports.catalog import AircraftRepository, AirportRepository
from charteros.domain.aircraft import AircraftId
from charteros.domain.missions import Mission
from charteros.domain.operators import OperatorId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import flight_minutes, haversine_distance_tenths_nm

CAPACITY_POLICY_VERSION = "aircraft-capacity-v1"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AircraftCapacityPlan:
    interval: TimeRange
    policy_version: str
    reference_profile_id: UUID
    reference_profile_recorded_at: datetime
    route_distance_tenths_nm: int
    route_minutes: int
    turnaround_buffer_minutes: int


class AircraftCapacityPolicy:
    def __init__(
        self,
        *,
        aircraft: AircraftRepository,
        airports: AirportRepository,
        references: CapacityReferenceRepository,
    ) -> None:
        self._aircraft = aircraft
        self._airports = airports
        self._references = references

    def derive(
        self,
        *,
        mission: Mission,
        aircraft_id: AircraftId,
        operator_id: OperatorId,
        known_as_of: datetime,
    ) -> AircraftCapacityPlan:
        decision_time = _utc(known_as_of, field_name="known_as_of")
        aircraft = self._aircraft.get(aircraft_id)
        if aircraft is None:
            raise EntityNotFoundError("quoted aircraft does not exist")
        if aircraft.operator_id != operator_id:
            raise EntityConflictError("quoted aircraft no longer belongs to the quoted operator")

        origin = self._airports.get(mission.origin_airport_id)
        destination = self._airports.get(mission.destination_airport_id)
        if origin is None or destination is None:
            raise EntityNotFoundError("mission airport dependency does not exist")

        profile = self._references.get_for_aircraft_type(
            aircraft.aircraft_type_id,
            known_as_of=decision_time,
        )
        if profile is None:
            raise EntityConflictError(
                "aircraft matching reference profile is required to reserve capacity"
            )

        route_distance = haversine_distance_tenths_nm(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        route_minutes = flight_minutes(route_distance, profile.cruise_speed_kts)
        capacity_end = mission.departure_window.end + timedelta(
            minutes=route_minutes + profile.turnaround_buffer_minutes
        )
        return AircraftCapacityPlan(
            interval=TimeRange(mission.departure_window.start, capacity_end),
            policy_version=CAPACITY_POLICY_VERSION,
            reference_profile_id=profile.id.value,
            reference_profile_recorded_at=profile.recorded_at,
            route_distance_tenths_nm=route_distance,
            route_minutes=route_minutes,
            turnaround_buffer_minutes=profile.turnaround_buffer_minutes,
        )
