from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.catalog import AircraftTypeSpec, CatalogService
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.aircraft import AircraftStatus
from charteros.domain.airports import AirportId
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.organizations import (
    OrganizationId,
    OrganizationStatus,
    OrganizationType,
)
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyAircraftTypeRepository,
    SqlAlchemyAirportRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
)
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["catalog"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OrganizationType
    legal_name: str = Field(min_length=2, max_length=200)
    trading_name: str | None = Field(default=None, min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=2)
    status: OrganizationStatus = OrganizationStatus.ACTIVE


class OrganizationResponse(BaseModel):
    id: UUID
    version: int
    type: OrganizationType
    legal_name: str
    trading_name: str | None
    country: str
    status: OrganizationStatus


class OperatorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    aoc_reference: str = Field(min_length=1, max_length=80)
    operating_regions: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1,
        max_length=64,
    )
    verification_status: VerificationStatus = VerificationStatus.PENDING
    insurance_status: InsuranceStatus = InsuranceStatus.UNKNOWN
    safety_documents: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list,
        max_length=128,
    )
    commercial_status: CommercialStatus = CommercialStatus.ACTIVE


class OperatorResponse(BaseModel):
    id: UUID
    version: int
    organization_id: UUID
    aoc_reference: str
    operating_regions: list[str]
    verification_status: VerificationStatus
    insurance_status: InsuranceStatus
    safety_documents: list[str]
    commercial_status: CommercialStatus


class AirportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    icao: str = Field(min_length=4, max_length=4)
    iata: str | None = Field(default=None, min_length=3, max_length=3)
    lat: Decimal = Field(ge=Decimal("-90"), le=Decimal("90"))
    lon: Decimal = Field(ge=Decimal("-180"), le=Decimal("180"))
    timezone: str = Field(min_length=1, max_length=64)
    runway_metadata: dict[str, object] = Field(default_factory=dict)
    curfew_metadata: dict[str, object] = Field(default_factory=dict)
    operational_flags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list,
        max_length=128,
    )


class AirportResponse(BaseModel):
    id: UUID
    version: int
    icao: str
    iata: str | None
    lat: Decimal
    lon: Decimal
    timezone: str
    runway_metadata: dict[str, object]
    curfew_metadata: dict[str, object]
    operational_flags: list[str]


class AircraftTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manufacturer: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    seats_min: int = Field(ge=0, le=1000)
    seats_max: int = Field(ge=1, le=1000)
    range_nm: int = Field(gt=0, le=50_000)
    runway_requirements: dict[str, object] = Field(default_factory=dict)
    baggage_cargo_profile: dict[str, object] = Field(default_factory=dict)


class AircraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_id: UUID
    registration: str = Field(min_length=2, max_length=16)
    aircraft_type: AircraftTypeCreate
    seat_capacity: int = Field(gt=0, le=1000)
    cargo_capacity: Decimal = Field(ge=Decimal("0"))
    range_nm: int = Field(gt=0, le=50_000)
    home_base: UUID
    status: AircraftStatus = AircraftStatus.ACTIVE


class AircraftResponse(BaseModel):
    id: UUID
    version: int
    operator_id: UUID
    registration: str
    aircraft_type_id: UUID
    seat_capacity: int
    cargo_capacity: Decimal
    range_nm: int
    home_base: UUID
    status: AircraftStatus


def _service(session: Session) -> CatalogService:
    return CatalogService(
        organizations=SqlAlchemyOrganizationRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        aircraft_types=SqlAlchemyAircraftTypeRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _get_stored_response(
    repository: IdempotencyRepository,
    *,
    scope: str,
    key: str,
    request_hash: str,
) -> StoredResponse | None:
    stored = repository.get(scope, key)
    if stored is None:
        return None
    if stored.request_hash != request_hash:
        raise EntityConflictError("idempotency key was already used with a different request body")
    return stored


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_organization(
    body: OrganizationCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> OrganizationResponse:
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock("POST:/v1/organizations", idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope="POST:/v1/organizations",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return OrganizationResponse.model_validate(stored.response_body)

        organization = _service(session).create_organization(
            organization_type=body.type,
            legal_name=body.legal_name,
            trading_name=body.trading_name,
            country=body.country,
            status=body.status,
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = OrganizationResponse(
            id=organization.id.value,
            version=organization.version,
            type=organization.organization_type,
            legal_name=organization.legal_name,
            trading_name=organization.trading_name,
            country=organization.country,
            status=organization.status,
        )
        idempotency.add(
            scope="POST:/v1/organizations",
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/operators",
    response_model=OperatorResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_operator(
    body: OperatorCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> OperatorResponse:
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock("POST:/v1/operators", idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope="POST:/v1/operators",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return OperatorResponse.model_validate(stored.response_body)

        operator = _service(session).create_operator(
            organization_id=OrganizationId(body.organization_id),
            aoc_reference=body.aoc_reference,
            operating_regions=tuple(body.operating_regions),
            verification_status=body.verification_status,
            insurance_status=body.insurance_status,
            safety_documents=tuple(body.safety_documents),
            commercial_status=body.commercial_status,
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = OperatorResponse(
            id=operator.id.value,
            version=operator.version,
            organization_id=operator.organization_id.value,
            aoc_reference=operator.aoc_reference,
            operating_regions=list(operator.operating_regions),
            verification_status=operator.verification_status,
            insurance_status=operator.insurance_status,
            safety_documents=list(operator.safety_documents),
            commercial_status=operator.commercial_status,
        )
        idempotency.add(
            scope="POST:/v1/operators",
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/airports",
    response_model=AirportResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_airport(
    body: AirportCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> AirportResponse:
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock("POST:/v1/airports", idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope="POST:/v1/airports",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return AirportResponse.model_validate(stored.response_body)

        airport = _service(session).create_airport(
            icao=body.icao,
            iata=body.iata,
            latitude=body.lat,
            longitude=body.lon,
            timezone=body.timezone,
            runway_metadata=body.runway_metadata,
            curfew_metadata=body.curfew_metadata,
            operational_flags=tuple(body.operational_flags),
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = AirportResponse(
            id=airport.id.value,
            version=airport.version,
            icao=airport.icao,
            iata=airport.iata,
            lat=airport.latitude,
            lon=airport.longitude,
            timezone=airport.timezone,
            runway_metadata=airport.runway_metadata,
            curfew_metadata=airport.curfew_metadata,
            operational_flags=list(airport.operational_flags),
        )
        idempotency.add(
            scope="POST:/v1/airports",
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/aircraft",
    response_model=AircraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_aircraft(
    body: AircraftCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> AircraftResponse:
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    type_spec = AircraftTypeSpec(
        manufacturer=body.aircraft_type.manufacturer,
        model=body.aircraft_type.model,
        category=body.aircraft_type.category,
        seats_min=body.aircraft_type.seats_min,
        seats_max=body.aircraft_type.seats_max,
        range_nm=body.aircraft_type.range_nm,
        runway_requirements=body.aircraft_type.runway_requirements,
        baggage_cargo_profile=body.aircraft_type.baggage_cargo_profile,
    )
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock("POST:/v1/aircraft", idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope="POST:/v1/aircraft",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return AircraftResponse.model_validate(stored.response_body)

        aircraft, _ = _service(session).create_aircraft(
            operator_id=OperatorId(body.operator_id),
            registration=body.registration,
            aircraft_type_spec=type_spec,
            seat_capacity=body.seat_capacity,
            cargo_capacity=body.cargo_capacity,
            range_nm=body.range_nm,
            home_base_id=AirportId(body.home_base),
            status=body.status,
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = AircraftResponse(
            id=aircraft.id.value,
            version=aircraft.version,
            operator_id=aircraft.operator_id.value,
            registration=aircraft.registration,
            aircraft_type_id=aircraft.aircraft_type_id.value,
            seat_capacity=aircraft.seat_capacity,
            cargo_capacity=aircraft.cargo_capacity,
            range_nm=aircraft.range_nm,
            home_base=aircraft.home_base_id.value,
            status=aircraft.status,
        )
        idempotency.add(
            scope="POST:/v1/aircraft",
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response
