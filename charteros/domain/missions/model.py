from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.airports import AirportId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange


class MissionId(TypedId):
    __slots__ = ()


class MissionStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    SOURCING = "sourcing"
    QUOTED = "quoted"
    SELECTED = "selected"
    CONTRACTING = "contracting"
    BOOKED = "booked"
    OPERATING = "operating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


def _canonical_requirements(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > 64:
        raise DomainValidationError("special_requirements cannot contain more than 64 entries")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(raw.split())
        if not 1 <= len(value) <= 200:
            raise DomainValidationError("each special requirement must contain 1 to 200 characters")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class Mission(AggregateRoot[MissionId]):
    aggregate_type = "mission"

    def __init__(
        self,
        mission_id: MissionId,
        *,
        buyer_id: OrganizationId,
        origin_airport_id: AirportId,
        destination_airport_id: AirportId,
        departure_window: TimeRange,
        passenger_count: int,
        max_budget: Money | None,
        special_requirements: tuple[str, ...],
        status: MissionStatus,
        version: int = 0,
    ) -> None:
        super().__init__(mission_id, version=version)
        if origin_airport_id == destination_airport_id:
            raise DomainValidationError("mission origin and destination must differ")
        if not isinstance(passenger_count, int) or isinstance(passenger_count, bool):
            raise DomainValidationError("passenger_count must be an integer")
        if passenger_count <= 0:
            raise DomainValidationError("passenger_count must be positive")
        if max_budget is not None and max_budget.amount_minor <= 0:
            raise DomainValidationError("max_budget must be positive when provided")

        self.buyer_id = buyer_id
        self.origin_airport_id = origin_airport_id
        self.destination_airport_id = destination_airport_id
        self.departure_window = departure_window
        self.passenger_count = passenger_count
        self.max_budget = max_budget
        self.special_requirements = _canonical_requirements(special_requirements)
        self.status = MissionStatus(status)

    @classmethod
    def create(
        cls,
        *,
        buyer_id: OrganizationId,
        origin_airport_id: AirportId,
        destination_airport_id: AirportId,
        departure_window: TimeRange,
        passenger_count: int,
        recorded_at: datetime,
        max_budget: Money | None = None,
        special_requirements: tuple[str, ...] = (),
        correlation_id: CorrelationId | None = None,
    ) -> Mission:
        mission = cls(
            MissionId.new(),
            buyer_id=buyer_id,
            origin_airport_id=origin_airport_id,
            destination_airport_id=destination_airport_id,
            departure_window=departure_window,
            passenger_count=passenger_count,
            max_budget=max_budget,
            special_requirements=special_requirements,
            status=MissionStatus.DRAFT,
        )
        mission._record_event(
            "MISSION_CREATED",
            {
                "buyer_id": str(mission.buyer_id),
                "origin_airport_id": str(mission.origin_airport_id),
                "destination_airport_id": str(mission.destination_airport_id),
                "departure_from": _iso(mission.departure_window.start),
                "departure_to": _iso(mission.departure_window.end),
                "passenger_count": mission.passenger_count,
                "max_budget_amount_minor": (
                    mission.max_budget.amount_minor if mission.max_budget is not None else None
                ),
                "max_budget_currency": (
                    str(mission.max_budget.currency) if mission.max_budget is not None else None
                ),
                "special_requirements": mission.special_requirements,
                "status": mission.status.value,
            },
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        return mission

    def open(self, *, recorded_at: datetime, correlation_id: CorrelationId | None = None) -> None:
        if self.status is not MissionStatus.DRAFT:
            raise DomainValidationError("only draft missions can be opened")
        self.status = MissionStatus.OPEN
        self._record_event(
            "MISSION_OPENED",
            {"status": self.status.value},
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )

    def start_sourcing(
        self, *, recorded_at: datetime, correlation_id: CorrelationId | None = None
    ) -> None:
        if self.status is not MissionStatus.OPEN:
            raise DomainValidationError("only open missions can enter sourcing")
        self.status = MissionStatus.SOURCING
        self._record_event(
            "MISSION_SOURCING",
            {"status": self.status.value},
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )

    def select_quote(
        self,
        *,
        quote_id: str,
        booking_id: str,
        selected_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status not in (MissionStatus.SOURCING, MissionStatus.QUOTED):
            raise DomainValidationError("only sourcing or quoted missions can select a quote")
        if not quote_id.strip():
            raise DomainValidationError("quote_id is required")
        if not booking_id.strip():
            raise DomainValidationError("booking_id is required")
        when = _utc(selected_at, field_name="selected_at")
        if self.status is MissionStatus.SOURCING:
            self.status = MissionStatus.QUOTED
            self._record_event(
                "MISSION_QUOTED",
                {"status": self.status.value},
                correlation_id=correlation_id,
                recorded_at=when,
                occurred_at=when,
            )
        self.status = MissionStatus.SELECTED
        self._record_event(
            "MISSION_SELECTED",
            {
                "status": self.status.value,
                "quote_id": quote_id,
                "booking_id": booking_id,
                "selected_at": _iso(when),
            },
            correlation_id=correlation_id,
            recorded_at=when,
            occurred_at=when,
        )

    def begin_contracting(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._workflow_transition(
            expected=MissionStatus.SELECTED,
            target=MissionStatus.CONTRACTING,
            event_type="MISSION_CONTRACTING",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def mark_booked(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._workflow_transition(
            expected=MissionStatus.CONTRACTING,
            target=MissionStatus.BOOKED,
            event_type="MISSION_BOOKED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def start_operating(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._workflow_transition(
            expected=MissionStatus.BOOKED,
            target=MissionStatus.OPERATING,
            event_type="MISSION_OPERATING",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def complete(
        self,
        *,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._workflow_transition(
            expected=MissionStatus.OPERATING,
            target=MissionStatus.COMPLETED,
            event_type="MISSION_COMPLETED",
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )

    def _workflow_transition(
        self,
        *,
        expected: MissionStatus,
        target: MissionStatus,
        event_type: str,
        transitioned_at: datetime,
        correlation_id: CorrelationId | None,
    ) -> None:
        if self.status is not expected:
            raise DomainValidationError(
                f"mission must be {expected.value} before transition to {target.value}"
            )
        when = _utc(transitioned_at, field_name="transitioned_at")
        self.status = target
        self._record_event(
            event_type,
            {
                "status": target.value,
                "transitioned_at": _iso(when),
            },
            correlation_id=correlation_id,
            recorded_at=when,
            occurred_at=when,
        )
