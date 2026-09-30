from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports import (
    AircraftRepository,
    AircraftTypeRepository,
    AirportRepository,
    DomainEventRepository,
    OperatorRepository,
    OrganizationRepository,
)
from charteros.domain.aircraft import Aircraft, AircraftStatus, AircraftType
from charteros.domain.airports import Airport, AirportId
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    Operator,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.organizations import (
    Organization,
    OrganizationId,
    OrganizationStatus,
    OrganizationType,
)
from charteros.domain.shared.ids import CorrelationId


@dataclass(frozen=True, slots=True)
class AircraftTypeSpec:
    manufacturer: str
    model: str
    category: str
    seats_min: int
    seats_max: int
    range_nm: int
    runway_requirements: dict[str, object]
    baggage_cargo_profile: dict[str, object]


class CatalogService:
    def __init__(
        self,
        *,
        organizations: OrganizationRepository,
        operators: OperatorRepository,
        airports: AirportRepository,
        aircraft_types: AircraftTypeRepository,
        aircraft: AircraftRepository,
        events: DomainEventRepository,
    ) -> None:
        self._organizations = organizations
        self._operators = operators
        self._airports = airports
        self._aircraft_types = aircraft_types
        self._aircraft = aircraft
        self._events = events

    def create_organization(
        self,
        *,
        organization_type: OrganizationType,
        legal_name: str,
        trading_name: str | None,
        country: str,
        status: OrganizationStatus,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> Organization:
        organization = Organization.create(
            organization_type=organization_type,
            legal_name=legal_name,
            trading_name=trading_name,
            country=country,
            status=status,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._organizations.add(organization)
        self._events.add_aggregate_events(organization)
        return organization

    def create_operator(
        self,
        *,
        organization_id: OrganizationId,
        aoc_reference: str,
        operating_regions: tuple[str, ...],
        verification_status: VerificationStatus,
        insurance_status: InsuranceStatus,
        safety_documents: tuple[str, ...],
        commercial_status: CommercialStatus,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> Operator:
        organization = self._organizations.get(organization_id)
        if organization is None:
            raise EntityNotFoundError("organization does not exist")
        if organization.organization_type is not OrganizationType.OPERATOR:
            raise EntityConflictError(
                "operator profile requires an organization of type 'operator'"
            )

        operator = Operator.create(
            organization_id=organization_id,
            aoc_reference=aoc_reference,
            operating_regions=operating_regions,
            verification_status=verification_status,
            insurance_status=insurance_status,
            safety_documents=safety_documents,
            commercial_status=commercial_status,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._operators.add(operator)
        self._events.add_aggregate_events(operator)
        return operator

    def create_airport(
        self,
        *,
        icao: str,
        iata: str | None,
        latitude: Decimal,
        longitude: Decimal,
        timezone: str,
        runway_metadata: dict[str, object],
        curfew_metadata: dict[str, object],
        operational_flags: tuple[str, ...],
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> Airport:
        airport = Airport.create(
            icao=icao,
            iata=iata,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
            runway_metadata=runway_metadata,
            curfew_metadata=curfew_metadata,
            operational_flags=operational_flags,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._airports.add(airport)
        self._events.add_aggregate_events(airport)
        return airport

    def create_aircraft(
        self,
        *,
        operator_id: OperatorId,
        registration: str,
        aircraft_type_spec: AircraftTypeSpec,
        seat_capacity: int,
        cargo_capacity: Decimal,
        range_nm: int,
        home_base_id: AirportId,
        status: AircraftStatus,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> tuple[Aircraft, AircraftType]:
        if self._operators.get(operator_id) is None:
            raise EntityNotFoundError("operator does not exist")
        if self._airports.get(home_base_id) is None:
            raise EntityNotFoundError("home base airport does not exist")

        aircraft_type = self._aircraft_types.find_by_make_model(
            aircraft_type_spec.manufacturer,
            aircraft_type_spec.model,
        )
        if aircraft_type is None:
            aircraft_type = AircraftType.create(
                manufacturer=aircraft_type_spec.manufacturer,
                model=aircraft_type_spec.model,
                category=aircraft_type_spec.category,
                seats_min=aircraft_type_spec.seats_min,
                seats_max=aircraft_type_spec.seats_max,
                range_nm=aircraft_type_spec.range_nm,
                runway_requirements=aircraft_type_spec.runway_requirements,
                baggage_cargo_profile=aircraft_type_spec.baggage_cargo_profile,
            )
            self._aircraft_types.add(aircraft_type)
        elif (
            aircraft_type.category != " ".join(aircraft_type_spec.category.split()).lower()
            or aircraft_type.seats_min != aircraft_type_spec.seats_min
            or aircraft_type.seats_max != aircraft_type_spec.seats_max
            or aircraft_type.range_nm != aircraft_type_spec.range_nm
            or aircraft_type.runway_requirements != aircraft_type_spec.runway_requirements
            or aircraft_type.baggage_cargo_profile != aircraft_type_spec.baggage_cargo_profile
        ):
            raise EntityConflictError(
                "aircraft type manufacturer/model already exists with different canonical data"
            )

        aircraft = Aircraft.create(
            operator_id=operator_id,
            registration=registration,
            aircraft_type=aircraft_type,
            seat_capacity=seat_capacity,
            cargo_capacity=cargo_capacity,
            range_nm=range_nm,
            home_base_id=home_base_id,
            status=status,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._aircraft.add(aircraft)
        self._events.add_aggregate_events(aircraft)
        return aircraft, aircraft_type
