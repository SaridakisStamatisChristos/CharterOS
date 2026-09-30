from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.aircraft import AircraftTypeId
from charteros.domain.bookings import BookingId
from charteros.domain.capacity_reservations import AircraftCapacityReservation
from charteros.matching import MatchingReferenceProfile


class AircraftCapacityReservationRepository(Protocol):
    def add(self, reservation: AircraftCapacityReservation) -> None: ...

    def get_for_booking(self, booking_id: BookingId) -> AircraftCapacityReservation | None: ...

    def get_for_booking_for_update(
        self,
        booking_id: BookingId,
    ) -> AircraftCapacityReservation | None: ...

    def save(
        self,
        reservation: AircraftCapacityReservation,
        *,
        expected_version: int,
    ) -> None: ...


class CapacityReferenceRepository(Protocol):
    def get_for_aircraft_type(
        self,
        aircraft_type_id: AircraftTypeId,
        *,
        known_as_of: datetime,
    ) -> MatchingReferenceProfile | None: ...
