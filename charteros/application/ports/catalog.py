from __future__ import annotations

from typing import Protocol

from charteros.domain.aircraft import Aircraft, AircraftType, AircraftTypeId
from charteros.domain.airports import Airport, AirportId
from charteros.domain.missions import Mission
from charteros.domain.operators import Operator, OperatorId
from charteros.domain.organizations import Organization, OrganizationId


class OrganizationRepository(Protocol):
    def add(self, organization: Organization) -> None: ...
    def get(self, organization_id: OrganizationId) -> Organization | None: ...


class OperatorRepository(Protocol):
    def add(self, operator: Operator) -> None: ...
    def get(self, operator_id: OperatorId) -> Operator | None: ...


class AirportRepository(Protocol):
    def add(self, airport: Airport) -> None: ...
    def get(self, airport_id: AirportId) -> Airport | None: ...


class AircraftTypeRepository(Protocol):
    def add(self, aircraft_type: AircraftType) -> None: ...
    def find_by_make_model(self, manufacturer: str, model: str) -> AircraftType | None: ...
    def get(self, aircraft_type_id: AircraftTypeId) -> AircraftType | None: ...


class AircraftRepository(Protocol):
    def add(self, aircraft: Aircraft) -> None: ...


class DomainEventRepository(Protocol):
    def add_aggregate_events(
        self,
        aggregate: Organization | Operator | Airport | Aircraft | Mission,
    ) -> None: ...
