from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.aircraft import (
    Aircraft,
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AvailabilityRecordId,
)
from charteros.domain.shared.time_range import TimeRange


class FleetAircraftRepository(Protocol):
    def get(self, aircraft_id: AircraftId) -> Aircraft | None: ...

    def get_for_update(self, aircraft_id: AircraftId) -> Aircraft | None: ...

    def save_version(self, aircraft: Aircraft, *, expected_version: int) -> None: ...


class FleetTimelineRepository(Protocol):
    def add_position(self, observation: AircraftPositionObservation) -> None: ...

    def add_availability(self, record: AircraftAvailabilityRecord) -> None: ...

    def get_availability(
        self, record_id: AvailabilityRecordId
    ) -> AircraftAvailabilityRecord | None: ...

    def find_current_overlaps(
        self,
        aircraft_id: AircraftId,
        interval: TimeRange,
        *,
        exclude_id: AvailabilityRecordId | None = None,
    ) -> tuple[AircraftAvailabilityRecord, ...]: ...

    def mark_superseded(
        self, record_id: AvailabilityRecordId, *, superseded_at: datetime
    ) -> None: ...

    def list_positions(
        self,
        aircraft_id: AircraftId,
        *,
        window: TimeRange,
        known_as_of: datetime,
        limit: int,
    ) -> tuple[tuple[AircraftPositionObservation, ...], bool]: ...

    def list_availability(
        self,
        aircraft_id: AircraftId,
        *,
        window: TimeRange,
        known_as_of: datetime,
        limit: int,
    ) -> tuple[tuple[AircraftAvailabilityRecord, ...], bool]: ...

    def position_at(
        self,
        aircraft_id: AircraftId,
        *,
        event_time: datetime,
        known_as_of: datetime,
    ) -> AircraftPositionObservation | None: ...

    def availability_at(
        self,
        aircraft_id: AircraftId,
        *,
        event_time: datetime,
        known_as_of: datetime,
    ) -> AircraftAvailabilityRecord | None: ...
