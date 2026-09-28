from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import (
    Aircraft,
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AircraftStatus,
    AircraftTypeId,
    AvailabilityRecordId,
    AvailabilityStatus,
    PositionObservationId,
)
from charteros.domain.airports import AirportId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.catalog import AircraftRow
from charteros.infrastructure.db.models.fleet import (
    AircraftAvailabilityRecordRow,
    AircraftPositionObservationRow,
)


def _flush(session: Session, *, conflict_message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise EntityConflictError(conflict_message) from exc


def _aircraft_from_row(row: AircraftRow) -> Aircraft:
    return Aircraft(
        AircraftId(row.id),
        operator_id=OperatorId(row.operator_id),
        registration=row.registration,
        aircraft_type_id=AircraftTypeId(row.aircraft_type_id),
        seat_capacity=row.seat_capacity,
        cargo_capacity=row.cargo_capacity,
        range_nm=row.range_nm,
        home_base_id=AirportId(row.home_base_id),
        status=AircraftStatus(row.status),
        version=row.version,
    )


def _position_from_row(row: AircraftPositionObservationRow) -> AircraftPositionObservation:
    return AircraftPositionObservation(
        id=PositionObservationId(row.id),
        aircraft_id=AircraftId(row.aircraft_id),
        airport_id=AirportId(row.airport_id) if row.airport_id is not None else None,
        latitude=row.latitude,
        longitude=row.longitude,
        event_time=row.event_time,
        recorded_at=row.recorded_at,
        source=row.source,
        provenance=row.provenance,
        created_at=row.created_at,
    )


def _availability_from_row(row: AircraftAvailabilityRecordRow) -> AircraftAvailabilityRecord:
    return AircraftAvailabilityRecord(
        id=AvailabilityRecordId(row.id),
        aircraft_id=AircraftId(row.aircraft_id),
        interval=TimeRange(row.valid_from, row.valid_to),
        status=AvailabilityStatus(row.status),
        recorded_at=row.recorded_at,
        source=row.source,
        reason=row.reason,
        provenance=row.provenance,
        supersedes_id=(
            AvailabilityRecordId(row.supersedes_id) if row.supersedes_id is not None else None
        ),
        superseded_at=row.superseded_at,
        created_at=row.created_at,
    )


class SqlAlchemyFleetAircraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, aircraft_id: AircraftId) -> Aircraft | None:
        row = self._session.get(AircraftRow, aircraft_id.value)
        return _aircraft_from_row(row) if row is not None else None

    def get_for_update(self, aircraft_id: AircraftId) -> Aircraft | None:
        statement = (
            select(AircraftRow)
            .where(AircraftRow.id == aircraft_id.value)
            .with_for_update()
        )
        row = self._session.scalar(statement)
        return _aircraft_from_row(row) if row is not None else None

    def save_version(self, aircraft: Aircraft, *, expected_version: int) -> None:
        row = self._session.get(AircraftRow, aircraft.id.value)
        if row is None:
            raise OptimisticConcurrencyError("aircraft disappeared during timeline mutation")
        if row.version != expected_version:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version}, actual {row.version}"
            )
        row.version = aircraft.version
        self._session.flush()


class SqlAlchemyFleetTimelineRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_position(self, observation: AircraftPositionObservation) -> None:
        self._session.add(
            AircraftPositionObservationRow(
                id=observation.id.value,
                aircraft_id=observation.aircraft_id.value,
                airport_id=observation.airport_id.value if observation.airport_id else None,
                latitude=observation.latitude,
                longitude=observation.longitude,
                event_time=observation.event_time,
                recorded_at=observation.recorded_at,
                source=observation.source,
                provenance=dict(observation.provenance),
                created_at=observation.created_at,
            )
        )
        _flush(
            self._session,
            conflict_message="position observation conflicts with persisted state",
        )

    def add_availability(self, record: AircraftAvailabilityRecord) -> None:
        self._session.add(
            AircraftAvailabilityRecordRow(
                id=record.id.value,
                aircraft_id=record.aircraft_id.value,
                valid_from=record.interval.start,
                valid_to=record.interval.end,
                status=record.status.value,
                recorded_at=record.recorded_at,
                source=record.source,
                reason=record.reason,
                provenance=dict(record.provenance),
                supersedes_id=record.supersedes_id.value if record.supersedes_id else None,
                superseded_at=record.superseded_at,
                created_at=record.created_at,
            )
        )
        _flush(
            self._session,
            conflict_message="availability interval conflicts with authoritative persisted state",
        )

    def get_availability(
        self, record_id: AvailabilityRecordId
    ) -> AircraftAvailabilityRecord | None:
        row = self._session.get(AircraftAvailabilityRecordRow, record_id.value)
        return _availability_from_row(row) if row is not None else None

    def find_current_overlaps(
        self,
        aircraft_id: AircraftId,
        interval: TimeRange,
        *,
        exclude_id: AvailabilityRecordId | None = None,
    ) -> tuple[AircraftAvailabilityRecord, ...]:
        conditions = [
            AircraftAvailabilityRecordRow.aircraft_id == aircraft_id.value,
            AircraftAvailabilityRecordRow.superseded_at.is_(None),
            AircraftAvailabilityRecordRow.valid_from < interval.end,
            AircraftAvailabilityRecordRow.valid_to > interval.start,
        ]
        if exclude_id is not None:
            conditions.append(AircraftAvailabilityRecordRow.id != exclude_id.value)
        statement = select(AircraftAvailabilityRecordRow).where(*conditions).order_by(
            AircraftAvailabilityRecordRow.valid_from,
            AircraftAvailabilityRecordRow.recorded_at,
            AircraftAvailabilityRecordRow.id,
        )
        return tuple(_availability_from_row(row) for row in self._session.scalars(statement))

    def mark_superseded(self, record_id: AvailabilityRecordId, *, superseded_at: datetime) -> None:
        row = self._session.get(AircraftAvailabilityRecordRow, record_id.value)
        if row is None:
            raise EntityConflictError("availability record disappeared during correction")
        if row.superseded_at is not None:
            raise EntityConflictError("availability record has already been superseded")
        row.superseded_at = superseded_at
        self._session.flush()

    def list_positions(
        self,
        aircraft_id: AircraftId,
        *,
        window: TimeRange,
        known_as_of: datetime,
        limit: int,
    ) -> tuple[tuple[AircraftPositionObservation, ...], bool]:
        statement = (
            select(AircraftPositionObservationRow)
            .where(
                AircraftPositionObservationRow.aircraft_id == aircraft_id.value,
                AircraftPositionObservationRow.event_time >= window.start,
                AircraftPositionObservationRow.event_time < window.end,
                AircraftPositionObservationRow.recorded_at <= known_as_of,
            )
            .order_by(
                AircraftPositionObservationRow.event_time,
                AircraftPositionObservationRow.recorded_at,
                AircraftPositionObservationRow.id,
            )
            .limit(limit + 1)
        )
        rows = tuple(self._session.scalars(statement))
        return tuple(_position_from_row(row) for row in rows[:limit]), len(rows) > limit

    def list_availability(
        self,
        aircraft_id: AircraftId,
        *,
        window: TimeRange,
        known_as_of: datetime,
        limit: int,
    ) -> tuple[tuple[AircraftAvailabilityRecord, ...], bool]:
        statement = (
            select(AircraftAvailabilityRecordRow)
            .where(
                AircraftAvailabilityRecordRow.aircraft_id == aircraft_id.value,
                AircraftAvailabilityRecordRow.valid_from < window.end,
                AircraftAvailabilityRecordRow.valid_to > window.start,
                AircraftAvailabilityRecordRow.recorded_at <= known_as_of,
            )
            .order_by(
                AircraftAvailabilityRecordRow.valid_from,
                AircraftAvailabilityRecordRow.recorded_at,
                AircraftAvailabilityRecordRow.id,
            )
            .limit(limit + 1)
        )
        rows = tuple(self._session.scalars(statement))
        return tuple(_availability_from_row(row) for row in rows[:limit]), len(rows) > limit

    def position_at(
        self,
        aircraft_id: AircraftId,
        *,
        event_time: datetime,
        known_as_of: datetime,
    ) -> AircraftPositionObservation | None:
        statement = (
            select(AircraftPositionObservationRow)
            .where(
                AircraftPositionObservationRow.aircraft_id == aircraft_id.value,
                AircraftPositionObservationRow.event_time <= event_time,
                AircraftPositionObservationRow.recorded_at <= known_as_of,
            )
            .order_by(
                AircraftPositionObservationRow.event_time.desc(),
                AircraftPositionObservationRow.recorded_at.desc(),
                AircraftPositionObservationRow.id.desc(),
            )
            .limit(1)
        )
        row = self._session.scalar(statement)
        return _position_from_row(row) if row is not None else None

    def availability_at(
        self,
        aircraft_id: AircraftId,
        *,
        event_time: datetime,
        known_as_of: datetime,
    ) -> AircraftAvailabilityRecord | None:
        statement = (
            select(AircraftAvailabilityRecordRow)
            .where(
                AircraftAvailabilityRecordRow.aircraft_id == aircraft_id.value,
                AircraftAvailabilityRecordRow.valid_from <= event_time,
                AircraftAvailabilityRecordRow.valid_to > event_time,
                AircraftAvailabilityRecordRow.recorded_at <= known_as_of,
                or_(
                    AircraftAvailabilityRecordRow.superseded_at.is_(None),
                    AircraftAvailabilityRecordRow.superseded_at > known_as_of,
                ),
            )
            .order_by(
                AircraftAvailabilityRecordRow.recorded_at.desc(),
                AircraftAvailabilityRecordRow.id.desc(),
            )
            .limit(1)
        )
        row = self._session.scalar(statement)
        return _availability_from_row(row) if row is not None else None
