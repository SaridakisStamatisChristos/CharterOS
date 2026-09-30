from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.application.ports.capacity import CapacityReferenceRepository
from charteros.domain.aircraft import AircraftId, AircraftTypeId
from charteros.domain.bookings import BookingId
from charteros.domain.capacity_reservations import (
    AircraftCapacityReservation,
    AircraftCapacityReservationId,
    AircraftCapacityReservationStatus,
)
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.capacity import AircraftCapacityReservationRow
from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.matching import MatchingProfileId, MatchingReferenceProfile


def _to_domain(row: AircraftCapacityReservationRow) -> AircraftCapacityReservation:
    return AircraftCapacityReservation(
        AircraftCapacityReservationId(row.id),
        aircraft_id=AircraftId(row.aircraft_id),
        booking_id=BookingId(row.booking_id),
        mission_id=MissionId(row.mission_id),
        operator_id=OperatorId(row.operator_id),
        interval=TimeRange(row.starts_at, row.ends_at),
        status=AircraftCapacityReservationStatus(row.status),
        created_at=row.created_at,
        released_at=row.released_at,
        release_reason=row.release_reason,
        policy_version=row.policy_version,
        reference_profile_id=row.reference_profile_id,
        reference_profile_recorded_at=row.reference_profile_recorded_at,
        route_distance_tenths_nm=row.route_distance_tenths_nm,
        route_minutes=row.route_minutes,
        turnaround_buffer_minutes=row.turnaround_buffer_minutes,
        version=row.version,
    )


class SqlAlchemyAircraftCapacityReservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reservation: AircraftCapacityReservation) -> None:
        self._session.add(
            AircraftCapacityReservationRow(
                id=reservation.id.value,
                version=reservation.version,
                aircraft_id=reservation.aircraft_id.value,
                booking_id=reservation.booking_id.value,
                mission_id=reservation.mission_id.value,
                operator_id=reservation.operator_id.value,
                starts_at=reservation.interval.start,
                ends_at=reservation.interval.end,
                status=reservation.status.value,
                created_at=reservation.created_at,
                released_at=reservation.released_at,
                release_reason=reservation.release_reason,
                policy_version=reservation.policy_version,
                reference_profile_id=reservation.reference_profile_id,
                reference_profile_recorded_at=reservation.reference_profile_recorded_at,
                route_distance_tenths_nm=reservation.route_distance_tenths_nm,
                route_minutes=reservation.route_minutes,
                turnaround_buffer_minutes=reservation.turnaround_buffer_minutes,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint_name == "ex_aircraft_capacity_reservations_reserved_overlap":
                raise EntityConflictError(
                    "aircraft is already committed to overlapping charter capacity"
                ) from exc
            raise EntityConflictError(
                "aircraft capacity reservation conflicts with persisted state"
            ) from exc

    def get_for_booking(self, booking_id: BookingId) -> AircraftCapacityReservation | None:
        row = self._session.scalar(
            select(AircraftCapacityReservationRow).where(
                AircraftCapacityReservationRow.booking_id == booking_id.value
            )
        )
        return _to_domain(row) if row is not None else None

    def get_for_booking_for_update(
        self,
        booking_id: BookingId,
    ) -> AircraftCapacityReservation | None:
        row = self._session.scalar(
            select(AircraftCapacityReservationRow)
            .where(AircraftCapacityReservationRow.booking_id == booking_id.value)
            .with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def save(
        self,
        reservation: AircraftCapacityReservation,
        *,
        expected_version: int,
    ) -> None:
        statement = (
            update(AircraftCapacityReservationRow)
            .where(
                AircraftCapacityReservationRow.id == reservation.id.value,
                AircraftCapacityReservationRow.version == expected_version,
            )
            .values(
                version=reservation.version,
                status=reservation.status.value,
                released_at=reservation.released_at,
                release_reason=reservation.release_reason,
            )
            .returning(AircraftCapacityReservationRow.id)
        )
        updated_id = self._session.scalar(statement)
        if updated_id is None:
            raise OptimisticConcurrencyError(
                "aircraft capacity reservation changed concurrently"
            )
        self._session.flush()


class SqlAlchemyCapacityReferenceRepository(CapacityReferenceRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_aircraft_type(
        self,
        aircraft_type_id: AircraftTypeId,
        *,
        known_as_of: datetime,
    ) -> MatchingReferenceProfile | None:
        row = self._session.scalar(
            select(MatchingReferenceProfileRow)
            .where(
                MatchingReferenceProfileRow.aircraft_type_id == aircraft_type_id.value,
                MatchingReferenceProfileRow.recorded_at <= known_as_of,
                or_(
                    MatchingReferenceProfileRow.superseded_at.is_(None),
                    MatchingReferenceProfileRow.superseded_at > known_as_of,
                ),
            )
            .order_by(
                MatchingReferenceProfileRow.recorded_at.desc(),
                MatchingReferenceProfileRow.id.desc(),
            )
            .limit(1)
        )
        if row is None:
            return None
        return MatchingReferenceProfile(
            id=MatchingProfileId(row.id),
            aircraft_type_id=AircraftTypeId(row.aircraft_type_id),
            cruise_speed_kts=row.cruise_speed_kts,
            operating_cost_per_hour=Money(
                row.operating_cost_per_hour_minor,
                Currency(row.operating_cost_currency),
            ),
            max_reposition_nm=row.max_reposition_nm,
            turnaround_buffer_minutes=row.turnaround_buffer_minutes,
            source=row.source,
            provenance=row.provenance,
            recorded_at=row.recorded_at,
        )
