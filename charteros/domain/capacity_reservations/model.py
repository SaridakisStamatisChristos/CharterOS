from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.time_range import TimeRange


class AircraftCapacityReservationId(TypedId):
    __slots__ = ()


class AircraftCapacityReservationStatus(StrEnum):
    RESERVED = "reserved"
    RELEASED = "released"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class AircraftCapacityReservation(AggregateRoot[AircraftCapacityReservationId]):
    aggregate_type = "aircraft_capacity_reservation"

    def __init__(
        self,
        reservation_id: AircraftCapacityReservationId,
        *,
        aircraft_id: AircraftId,
        booking_id: BookingId,
        mission_id: MissionId,
        operator_id: OperatorId,
        interval: TimeRange,
        status: AircraftCapacityReservationStatus,
        created_at: datetime,
        policy_version: str,
        reference_profile_id: UUID,
        reference_profile_recorded_at: datetime,
        route_distance_tenths_nm: int,
        route_minutes: int,
        turnaround_buffer_minutes: int,
        released_at: datetime | None = None,
        release_reason: str | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(reservation_id, version=version)
        if not policy_version.strip():
            raise DomainValidationError("capacity reservation policy_version is required")
        if route_distance_tenths_nm < 0:
            raise DomainValidationError("route_distance_tenths_nm cannot be negative")
        if route_minutes < 0:
            raise DomainValidationError("route_minutes cannot be negative")
        if turnaround_buffer_minutes < 0:
            raise DomainValidationError("turnaround_buffer_minutes cannot be negative")
        created = _utc(created_at, field_name="created_at")
        profile_recorded = _utc(
            reference_profile_recorded_at,
            field_name="reference_profile_recorded_at",
        )
        normalized_status = AircraftCapacityReservationStatus(status)
        released = _utc(released_at, field_name="released_at") if released_at is not None else None
        normalized_reason = " ".join(release_reason.split()) if release_reason is not None else None
        if normalized_status is AircraftCapacityReservationStatus.RESERVED:
            if released is not None or normalized_reason is not None:
                raise DomainValidationError("reserved capacity cannot contain release evidence")
        elif released is None or not normalized_reason:
            raise DomainValidationError("released capacity requires release timestamp and reason")

        self.aircraft_id = aircraft_id
        self.booking_id = booking_id
        self.mission_id = mission_id
        self.operator_id = operator_id
        self.interval = interval
        self.status = normalized_status
        self.created_at = created
        self.released_at = released
        self.release_reason = normalized_reason
        self.policy_version = policy_version.strip()
        self.reference_profile_id = reference_profile_id
        self.reference_profile_recorded_at = profile_recorded
        self.route_distance_tenths_nm = route_distance_tenths_nm
        self.route_minutes = route_minutes
        self.turnaround_buffer_minutes = turnaround_buffer_minutes

    @classmethod
    def create(
        cls,
        *,
        aircraft_id: AircraftId,
        booking_id: BookingId,
        mission_id: MissionId,
        operator_id: OperatorId,
        interval: TimeRange,
        created_at: datetime,
        policy_version: str,
        reference_profile_id: UUID,
        reference_profile_recorded_at: datetime,
        route_distance_tenths_nm: int,
        route_minutes: int,
        turnaround_buffer_minutes: int,
        correlation_id: CorrelationId | None = None,
    ) -> AircraftCapacityReservation:
        reservation = cls(
            AircraftCapacityReservationId.new(),
            aircraft_id=aircraft_id,
            booking_id=booking_id,
            mission_id=mission_id,
            operator_id=operator_id,
            interval=interval,
            status=AircraftCapacityReservationStatus.RESERVED,
            created_at=created_at,
            policy_version=policy_version,
            reference_profile_id=reference_profile_id,
            reference_profile_recorded_at=reference_profile_recorded_at,
            route_distance_tenths_nm=route_distance_tenths_nm,
            route_minutes=route_minutes,
            turnaround_buffer_minutes=turnaround_buffer_minutes,
        )
        reservation._record_event(
            "AIRCRAFT_CAPACITY_RESERVED",
            {
                "aircraft_id": str(aircraft_id),
                "booking_id": str(booking_id),
                "mission_id": str(mission_id),
                "operator_id": str(operator_id),
                "starts_at": _iso(interval.start),
                "ends_at": _iso(interval.end),
                "status": reservation.status.value,
                "policy_version": reservation.policy_version,
                "reference_profile_id": str(reference_profile_id),
                "reference_profile_recorded_at": _iso(reference_profile_recorded_at),
                "route_distance_tenths_nm": route_distance_tenths_nm,
                "route_minutes": route_minutes,
                "turnaround_buffer_minutes": turnaround_buffer_minutes,
            },
            recorded_at=reservation.created_at,
            occurred_at=reservation.created_at,
            correlation_id=correlation_id,
        )
        return reservation
