from __future__ import annotations

from datetime import datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports import (
    AirportRepository,
    DomainEventRepository,
    MissionRepository,
    OrganizationRepository,
)
from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.organizations import OrganizationId, OrganizationStatus
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange


class MissionService:
    def __init__(
        self,
        *,
        missions: MissionRepository,
        organizations: OrganizationRepository,
        airports: AirportRepository,
        events: DomainEventRepository,
    ) -> None:
        self._missions = missions
        self._organizations = organizations
        self._airports = airports
        self._events = events

    def create_mission(
        self,
        *,
        buyer_id: OrganizationId,
        origin_airport_id: AirportId,
        destination_airport_id: AirportId,
        departure_window: TimeRange,
        passenger_count: int,
        max_budget: Money | None,
        special_requirements: tuple[str, ...],
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> Mission:
        buyer = self._organizations.get(buyer_id)
        if buyer is None:
            raise EntityNotFoundError("buyer organization does not exist")
        if buyer.status is not OrganizationStatus.ACTIVE:
            raise EntityConflictError("buyer organization must be active")
        if self._airports.get(origin_airport_id) is None:
            raise EntityNotFoundError("origin airport does not exist")
        if self._airports.get(destination_airport_id) is None:
            raise EntityNotFoundError("destination airport does not exist")

        mission = Mission.create(
            buyer_id=buyer_id,
            origin_airport_id=origin_airport_id,
            destination_airport_id=destination_airport_id,
            departure_window=departure_window,
            passenger_count=passenger_count,
            max_budget=max_budget,
            special_requirements=special_requirements,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._missions.add(mission)
        self._events.add_aggregate_events(mission)
        return mission

    def get_mission(self, mission_id: MissionId) -> Mission:
        mission = self._missions.get(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")
        return mission

    def open_mission(
        self,
        *,
        mission_id: MissionId,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> Mission:
        mission = self._missions.get_for_update(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")
        if mission.status is not MissionStatus.DRAFT:
            raise EntityConflictError("only draft missions can be opened")

        expected_version = mission.version
        mission.open(recorded_at=recorded_at, correlation_id=correlation_id)
        self._missions.save(mission, expected_version=expected_version)
        self._events.add_aggregate_events(mission)
        return mission
