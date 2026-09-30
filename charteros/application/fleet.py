from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import AirportRepository, DomainEventRepository
from charteros.application.ports.fleet import FleetAircraftRepository, FleetTimelineRepository
from charteros.domain.aircraft import (
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AvailabilityRecordId,
    AvailabilityStatus,
    ensure_utc,
)
from charteros.domain.airports import AirportId
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.time_range import TimeRange


@dataclass(frozen=True, slots=True)
class FleetState:
    event_time: datetime
    position: AircraftPositionObservation | None
    availability: AircraftAvailabilityRecord | None


@dataclass(frozen=True, slots=True)
class AircraftTimeline:
    aircraft_id: AircraftId
    window: TimeRange
    known_as_of: datetime
    positions: tuple[AircraftPositionObservation, ...]
    availability: tuple[AircraftAvailabilityRecord, ...]
    positions_truncated: bool
    availability_truncated: bool
    state: FleetState | None


class FleetTimelineService:
    def __init__(
        self,
        *,
        aircraft: FleetAircraftRepository,
        airports: AirportRepository,
        timeline: FleetTimelineRepository,
        events: DomainEventRepository,
    ) -> None:
        self._aircraft = aircraft
        self._airports = airports
        self._timeline = timeline
        self._events = events

    def record_position(
        self,
        *,
        aircraft_id: AircraftId,
        airport_id: AirportId | None,
        latitude: Decimal | None,
        longitude: Decimal | None,
        event_time: datetime,
        recorded_at: datetime,
        source: str,
        provenance: Mapping[str, object] | None,
        correlation_id: CorrelationId,
    ) -> tuple[AircraftPositionObservation, int]:
        aircraft = self._aircraft.get_for_update(aircraft_id)
        if aircraft is None:
            raise EntityNotFoundError("aircraft does not exist")
        if airport_id is not None and self._airports.get(airport_id) is None:
            raise EntityNotFoundError("position airport does not exist")

        expected_version = aircraft.version
        recorded = ensure_utc(recorded_at, field_name="recorded_at")
        observation = AircraftPositionObservation.create(
            aircraft_id=aircraft_id,
            airport_id=airport_id,
            latitude=latitude,
            longitude=longitude,
            event_time=event_time,
            recorded_at=recorded,
            source=source,
            provenance=provenance,
        )
        aircraft.record_position_observation(observation, correlation_id=correlation_id)
        self._timeline.add_position(observation)
        self._aircraft.save_version(aircraft, expected_version=expected_version)
        self._events.add_aggregate_events(aircraft)
        return observation, aircraft.version

    def record_availability(
        self,
        *,
        aircraft_id: AircraftId,
        valid_from: datetime,
        valid_to: datetime,
        status: AvailabilityStatus,
        source: str,
        reason: str | None,
        provenance: Mapping[str, object] | None,
        supersedes_id: AvailabilityRecordId | None,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> tuple[AircraftAvailabilityRecord, int]:
        aircraft = self._aircraft.get_for_update(aircraft_id)
        if aircraft is None:
            raise EntityNotFoundError("aircraft does not exist")

        expected_version = aircraft.version
        recorded = ensure_utc(recorded_at, field_name="recorded_at")
        record = AircraftAvailabilityRecord.create(
            aircraft_id=aircraft_id,
            valid_from=valid_from,
            valid_to=valid_to,
            status=status,
            recorded_at=recorded,
            source=source,
            reason=reason,
            provenance=provenance,
            supersedes_id=supersedes_id,
        )

        superseded: AircraftAvailabilityRecord | None = None
        if supersedes_id is not None:
            superseded = self._timeline.get_availability(supersedes_id)
            if superseded is None:
                raise EntityNotFoundError("availability record to supersede does not exist")
            if superseded.aircraft_id != aircraft_id:
                raise EntityConflictError(
                    "cannot supersede availability belonging to another aircraft"
                )
            if superseded.superseded_at is not None:
                raise EntityConflictError("availability record has already been superseded")

        overlaps = self._timeline.find_current_overlaps(
            aircraft_id,
            record.interval,
            exclude_id=superseded.id if superseded is not None else None,
        )
        if overlaps:
            raise EntityConflictError(
                "availability interval overlaps an existing authoritative interval; "
                "use supersedes_id for an explicit correction"
            )

        if superseded is not None:
            self._timeline.mark_superseded(superseded.id, superseded_at=recorded)
        self._timeline.add_availability(record)
        aircraft.record_availability_change(record, correlation_id=correlation_id)
        self._aircraft.save_version(aircraft, expected_version=expected_version)
        self._events.add_aggregate_events(aircraft)
        return record, aircraft.version

    def get_timeline(
        self,
        *,
        aircraft_id: AircraftId,
        from_time: datetime,
        to_time: datetime,
        known_as_of: datetime,
        state_at: datetime | None,
        limit: int,
    ) -> AircraftTimeline:
        if self._aircraft.get(aircraft_id) is None:
            raise EntityNotFoundError("aircraft does not exist")
        window = TimeRange(from_time, to_time)
        knowledge_time = ensure_utc(known_as_of, field_name="known_as_of")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        positions, positions_truncated = self._timeline.list_positions(
            aircraft_id,
            window=window,
            known_as_of=knowledge_time,
            limit=limit,
        )
        availability, availability_truncated = self._timeline.list_availability(
            aircraft_id,
            window=window,
            known_as_of=knowledge_time,
            limit=limit,
        )

        state: FleetState | None = None
        if state_at is not None:
            state_time = ensure_utc(state_at, field_name="at")
            state = FleetState(
                event_time=state_time,
                position=self._timeline.position_at(
                    aircraft_id,
                    event_time=state_time,
                    known_as_of=knowledge_time,
                ),
                availability=self._timeline.availability_at(
                    aircraft_id,
                    event_time=state_time,
                    known_as_of=knowledge_time,
                ),
            )

        return AircraftTimeline(
            aircraft_id=aircraft_id,
            window=window,
            known_as_of=knowledge_time,
            positions=positions,
            availability=availability,
            positions_truncated=positions_truncated,
            availability_truncated=availability_truncated,
            state=state,
        )
