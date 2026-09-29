from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.domain.aircraft import AircraftStatus
from charteros.domain.bookings import BookingState
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError

MAX_PORTAL_PAGE = 100
MAX_CALENDAR_WINDOW = timedelta(days=366)


@dataclass(frozen=True, slots=True)
class PortalAircraft:
    id: UUID
    version: int
    operator_id: UUID
    registration: str
    aircraft_type_id: UUID
    manufacturer: str
    model: str
    category: str
    seat_capacity: int
    cargo_capacity: Decimal
    range_nm: int
    home_base_id: UUID
    home_base_icao: str
    status: AircraftStatus


@dataclass(frozen=True, slots=True)
class PortalAircraftPage:
    items: tuple[PortalAircraft, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class PortalRfq:
    id: UUID
    version: int
    operator_id: UUID
    status: RfqStatus
    created_at: datetime
    sent_at: datetime | None
    response_deadline: datetime | None
    acknowledged_at: datetime | None
    declined_at: datetime | None
    expired_at: datetime | None
    decline_reason: str | None
    mission_id: UUID
    mission_status: str
    origin_airport_id: UUID
    origin_icao: str
    destination_airport_id: UUID
    destination_icao: str
    departure_from: datetime
    departure_to: datetime
    passenger_count: int
    special_requirements: tuple[str, ...]
    current_quote_id: UUID | None
    current_quote_status: str | None
    current_quote_revision: int | None
    tender_id: UUID | None
    tender_status: str | None
    tender_sealed_bid: bool | None
    tender_invitation_id: UUID | None
    tender_invitation_status: str | None


@dataclass(frozen=True, slots=True)
class PortalRfqPage:
    items: tuple[PortalRfq, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class PortalQuoteContext:
    quote_id: UUID
    rfq: PortalRfq


@dataclass(frozen=True, slots=True)
class PortalBooking:
    id: UUID
    version: int
    mission_id: UUID
    accepted_quote_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    state: BookingState
    created_at: datetime
    state_changed_at: datetime
    origin_airport_id: UUID
    origin_icao: str
    destination_airport_id: UUID
    destination_icao: str
    departure_from: datetime
    departure_to: datetime
    quote_currency: str


@dataclass(frozen=True, slots=True)
class PortalBookingPage:
    items: tuple[PortalBooking, ...]
    next_cursor: UUID | None


class OperatorPortalReadRepository(Protocol):
    def operator_exists(self, operator_id: OperatorId) -> bool: ...

    def list_aircraft(
        self,
        operator_id: OperatorId,
        *,
        limit: int,
        cursor: UUID | None,
    ) -> PortalAircraftPage: ...

    def get_aircraft(
        self,
        operator_id: OperatorId,
        aircraft_id: UUID,
    ) -> PortalAircraft | None: ...

    def list_rfqs(
        self,
        operator_id: OperatorId,
        *,
        statuses: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalRfqPage: ...

    def get_rfq(
        self,
        operator_id: OperatorId,
        rfq_id: UUID,
    ) -> PortalRfq | None: ...

    def get_quote_context(
        self,
        operator_id: OperatorId,
        quote_id: UUID,
    ) -> PortalQuoteContext | None: ...

    def list_bookings(
        self,
        operator_id: OperatorId,
        *,
        states: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage: ...

    def get_booking(
        self,
        operator_id: OperatorId,
        booking_id: UUID,
    ) -> PortalBooking | None: ...

    def calendar(
        self,
        operator_id: OperatorId,
        *,
        window_start: datetime,
        window_end: datetime,
        states: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage: ...


class OperatorPortalService:
    """Operator-scoped read facade and ownership/capability policy.

    ``X-Operator-Id`` is an explicit application context only. Authentication and
    credential verification remain deployment concerns and are not fabricated here.
    """

    def __init__(self, repository: OperatorPortalReadRepository) -> None:
        self._repository = repository

    def assert_operator(self, operator_id: OperatorId) -> None:
        if not self._repository.operator_exists(operator_id):
            raise EntityNotFoundError("operator context does not exist")

    def fleet(
        self,
        *,
        operator_id: OperatorId,
        limit: int,
        cursor: UUID | None,
    ) -> PortalAircraftPage:
        self.assert_operator(operator_id)
        return self._repository.list_aircraft(
            operator_id,
            limit=_limit(limit),
            cursor=cursor,
        )

    def aircraft(self, *, operator_id: OperatorId, aircraft_id: UUID) -> PortalAircraft:
        self.assert_operator(operator_id)
        item = self._repository.get_aircraft(operator_id, aircraft_id)
        if item is None:
            raise EntityNotFoundError("aircraft is not available in the operator context")
        return item

    def rfq_inbox(
        self,
        *,
        operator_id: OperatorId,
        statuses: tuple[RfqStatus, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalRfqPage:
        self.assert_operator(operator_id)
        return self._repository.list_rfqs(
            operator_id,
            statuses=tuple(item.value for item in statuses),
            limit=_limit(limit),
            cursor=cursor,
        )

    def rfq(
        self,
        *,
        operator_id: OperatorId,
        rfq_id: UUID,
        invitation_id: UUID | None = None,
        require_tender_capability: bool = False,
    ) -> PortalRfq:
        self.assert_operator(operator_id)
        item = self._repository.get_rfq(operator_id, rfq_id)
        if item is None:
            raise EntityNotFoundError("RFQ is not available in the operator context")
        if require_tender_capability or invitation_id is not None:
            self._assert_tender_capability(item, invitation_id)
        return item

    def quote_context(
        self,
        *,
        operator_id: OperatorId,
        quote_id: UUID,
        invitation_id: UUID | None = None,
    ) -> PortalQuoteContext:
        self.assert_operator(operator_id)
        item = self._repository.get_quote_context(operator_id, quote_id)
        if item is None:
            raise EntityNotFoundError("quote is not available in the operator context")
        if item.rfq.tender_invitation_id is not None or invitation_id is not None:
            self._assert_tender_capability(item.rfq, invitation_id)
        return item

    def bookings(
        self,
        *,
        operator_id: OperatorId,
        states: tuple[BookingState, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage:
        self.assert_operator(operator_id)
        return self._repository.list_bookings(
            operator_id,
            states=tuple(item.value for item in states),
            limit=_limit(limit),
            cursor=cursor,
        )

    def booking(self, *, operator_id: OperatorId, booking_id: UUID) -> PortalBooking:
        self.assert_operator(operator_id)
        item = self._repository.get_booking(operator_id, booking_id)
        if item is None:
            raise EntityNotFoundError("booking is not available in the operator context")
        return item

    def mission_calendar(
        self,
        *,
        operator_id: OperatorId,
        window_start: datetime,
        window_end: datetime,
        states: tuple[BookingState, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage:
        self.assert_operator(operator_id)
        start = _utc(window_start, field_name="window_start")
        end = _utc(window_end, field_name="window_end")
        if end <= start:
            raise DomainValidationError("window_end must be after window_start")
        if end - start > MAX_CALENDAR_WINDOW:
            raise DomainValidationError("mission calendar window cannot exceed 366 days")
        return self._repository.calendar(
            operator_id,
            window_start=start,
            window_end=end,
            states=tuple(item.value for item in states),
            limit=_limit(limit),
            cursor=cursor,
        )

    @staticmethod
    def _assert_tender_capability(item: PortalRfq, invitation_id: UUID | None) -> None:
        expected = item.tender_invitation_id
        if expected is None:
            if invitation_id is not None:
                raise EntityConflictError(
                    "tender invitation capability was supplied for a non-tender RFQ"
                )
            return
        if invitation_id is None or invitation_id != expected:
            raise EntityNotFoundError(
                "tender RFQ is not available for the supplied operator/invitation capability"
            )


def _limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= MAX_PORTAL_PAGE:
        raise DomainValidationError(f"limit must be between 1 and {MAX_PORTAL_PAGE}")
    return value


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
