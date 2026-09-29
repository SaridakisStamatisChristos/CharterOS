from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from charteros.application.exceptions import EntityNotFoundError
from charteros.domain.shared.exceptions import DomainValidationError

MAX_QUERY_LIMIT = 100
MAX_EMPTY_LEG_WINDOW = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class HistoricalPosition:
    aircraft_id: UUID
    position_id: UUID
    airport_id: UUID | None
    latitude: Decimal
    longitude: Decimal
    event_time: datetime
    knowledge_time: datetime
    source: str
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class NearbyAircraft:
    position: HistoricalPosition
    distance_nm: Decimal


@dataclass(frozen=True, slots=True)
class OperatorRoute:
    booking_id: UUID
    mission_id: UUID
    aircraft_id: UUID
    accepted_quote_id: UUID | None
    origin_airport_id: UUID
    origin_icao: str
    destination_airport_id: UUID
    destination_icao: str
    departure_from: datetime
    departure_to: datetime
    booking_state: str


@dataclass(frozen=True, slots=True)
class QuoteEventHistoryItem:
    event_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class QuoteRevision:
    quote_id: UUID
    revision_number: int
    status: str
    supersedes_quote_id: UUID | None


@dataclass(frozen=True, slots=True)
class QuoteHistory:
    quote_id: UUID
    rfq_id: UUID
    revisions: tuple[QuoteRevision, ...]
    events: tuple[QuoteEventHistoryItem, ...]


@dataclass(frozen=True, slots=True)
class EmptyLegCandidate:
    aircraft_id: UUID
    operator_id: UUID
    previous_booking_id: UUID
    previous_mission_id: UUID
    next_booking_id: UUID
    next_mission_id: UUID
    from_airport_id: UUID
    from_icao: str
    to_airport_id: UUID
    to_icao: str
    window_start: datetime
    window_end: datetime
    gap_minutes: int
    evidence_kind: str = "between_planned_bookings"


@dataclass(frozen=True, slots=True)
class BookingFlightLineage:
    booking_id: UUID
    mission_id: UUID
    accepted_quote_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    origin_airport_id: UUID
    origin_icao: str
    destination_airport_id: UUID
    destination_icao: str
    departure_from: datetime
    departure_to: datetime
    booking_state: str
    flight_entity_id: UUID | None = None
    lineage_status: str = "planned_route_only"


class GraphQueryReadRepository(Protocol):
    def active_version(self) -> int: ...

    def has_node(self, *, node_type: str, node_id: UUID) -> bool: ...

    def historical_position(
        self,
        *,
        aircraft_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
    ) -> HistoricalPosition | None: ...

    def nearby_aircraft(
        self,
        *,
        airport_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
        radius_nm: Decimal,
        limit: int,
    ) -> tuple[NearbyAircraft, ...]: ...

    def operator_route_history(
        self,
        *,
        operator_id: UUID,
        limit: int,
    ) -> tuple[OperatorRoute, ...]: ...

    def quote_history(self, *, quote_id: UUID) -> QuoteHistory | None: ...

    def empty_leg_candidates(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[EmptyLegCandidate, ...]: ...

    def booking_flight_lineage(
        self,
        *,
        booking_id: UUID,
    ) -> BookingFlightLineage | None: ...


class GraphQueryService:
    """Bounded read-only queries over the active Charter Graph projection.

    The service deliberately exposes typed operations rather than a graph query language.
    """

    def __init__(self, repository: GraphQueryReadRepository) -> None:
        self._repository = repository

    @property
    def projection_version(self) -> int:
        return self._repository.active_version()

    def assert_projected_mission(self, mission_id: UUID) -> int:
        version = self._repository.active_version()
        if not self._repository.has_node(node_type="mission", node_id=mission_id):
            raise EntityNotFoundError(
                "mission does not exist in the active Charter Graph projection"
            )
        return version

    def historical_position(
        self,
        *,
        aircraft_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
    ) -> HistoricalPosition:
        event_cutoff = _utc(event_time, field_name="event_time")
        knowledge_cutoff = _utc(known_as_of, field_name="known_as_of")
        item = self._repository.historical_position(
            aircraft_id=aircraft_id,
            event_time=event_cutoff,
            known_as_of=knowledge_cutoff,
        )
        if item is None:
            raise EntityNotFoundError(
                "no aircraft position is visible at the requested event/knowledge cutoffs"
            )
        return item

    def nearby_aircraft(
        self,
        *,
        airport_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
        radius_nm: Decimal,
        limit: int,
    ) -> tuple[NearbyAircraft, ...]:
        event_cutoff = _utc(event_time, field_name="event_time")
        knowledge_cutoff = _utc(known_as_of, field_name="known_as_of")
        if radius_nm <= 0 or radius_nm > Decimal("5000"):
            raise DomainValidationError("radius_nm must be greater than 0 and at most 5000")
        bounded = _limit(limit)
        return self._repository.nearby_aircraft(
            airport_id=airport_id,
            event_time=event_cutoff,
            known_as_of=knowledge_cutoff,
            radius_nm=radius_nm,
            limit=bounded,
        )

    def operator_route_history(
        self,
        *,
        operator_id: UUID,
        limit: int,
    ) -> tuple[OperatorRoute, ...]:
        return self._repository.operator_route_history(
            operator_id=operator_id,
            limit=_limit(limit),
        )

    def quote_history(self, *, quote_id: UUID) -> QuoteHistory:
        item = self._repository.quote_history(quote_id=quote_id)
        if item is None:
            raise EntityNotFoundError("quote does not exist in the active Charter Graph projection")
        return item

    def empty_leg_candidates(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[EmptyLegCandidate, ...]:
        start = _utc(window_start, field_name="window_start")
        end = _utc(window_end, field_name="window_end")
        if end <= start:
            raise DomainValidationError("window_end must be after window_start")
        if end - start > MAX_EMPTY_LEG_WINDOW:
            raise DomainValidationError("empty-leg query window cannot exceed 90 days")
        return self._repository.empty_leg_candidates(
            window_start=start,
            window_end=end,
            limit=_limit(limit),
        )

    def booking_flight_lineage(self, *, booking_id: UUID) -> BookingFlightLineage:
        item = self._repository.booking_flight_lineage(booking_id=booking_id)
        if item is None:
            raise EntityNotFoundError(
                "booking does not exist in the active Charter Graph projection"
            )
        return item


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= MAX_QUERY_LIMIT:
        raise DomainValidationError(f"limit must be between 1 and {MAX_QUERY_LIMIT}")
    return value
