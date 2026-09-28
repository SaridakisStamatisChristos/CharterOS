from __future__ import annotations

from typing import Protocol

from charteros.domain.bookings import Booking, BookingId
from charteros.domain.missions import MissionId


class BookingRepository(Protocol):
    def add(self, booking: Booking) -> None: ...

    def get(self, booking_id: BookingId) -> Booking | None: ...

    def get_for_mission(self, mission_id: MissionId) -> Booking | None: ...
