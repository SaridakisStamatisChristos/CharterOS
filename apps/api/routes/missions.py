from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.missions import MissionService
from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAirportRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOrganizationRepository,
)
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["missions"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class TimeWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime


class MoneyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_id: UUID
    origin_airport_id: UUID
    destination_airport_id: UUID
    departure_window: TimeWindowRequest
    passenger_count: int = Field(gt=0)
    max_budget: MoneyRequest | None = None
    special_requirements: list[str] = Field(default_factory=list, max_length=64)


class TimeWindowResponse(BaseModel):
    start: datetime
    end: datetime


class MoneyResponse(BaseModel):
    amount_minor: int
    currency: str


class MissionResponse(BaseModel):
    id: UUID
    version: int
    buyer_id: UUID
    origin_airport_id: UUID
    destination_airport_id: UUID
    departure_window: TimeWindowResponse
    passenger_count: int
    max_budget: MoneyResponse | None
    special_requirements: list[str]
    status: MissionStatus


def _service(session: Session) -> MissionService:
    return MissionService(
        missions=SqlAlchemyMissionRepository(session),
        organizations=SqlAlchemyOrganizationRepository(session),
        airports=SqlAlchemyAirportRepository(session),
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


def _response(mission: Mission) -> MissionResponse:
    budget = None
    if mission.max_budget is not None:
        budget = MoneyResponse(
            amount_minor=mission.max_budget.amount_minor,
            currency=str(mission.max_budget.currency),
        )
    return MissionResponse(
        id=mission.id.value,
        version=mission.version,
        buyer_id=mission.buyer_id.value,
        origin_airport_id=mission.origin_airport_id.value,
        destination_airport_id=mission.destination_airport_id.value,
        departure_window=TimeWindowResponse(
            start=mission.departure_window.start,
            end=mission.departure_window.end,
        ),
        passenger_count=mission.passenger_count,
        max_budget=budget,
        special_requirements=list(mission.special_requirements),
        status=mission.status,
    )


@router.post("/missions", response_model=MissionResponse, status_code=status.HTTP_201_CREATED)
def create_mission(
    body: MissionCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> MissionResponse:
    scope = "POST:/v1/missions"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return MissionResponse.model_validate(stored.response_body)

        budget = None
        if body.max_budget is not None:
            budget = Money(
                body.max_budget.amount_minor,
                Currency(body.max_budget.currency.strip().upper()),
            )
        mission = _service(session).create_mission(
            buyer_id=OrganizationId(body.buyer_id),
            origin_airport_id=AirportId(body.origin_airport_id),
            destination_airport_id=AirportId(body.destination_airport_id),
            departure_window=TimeRange(
                body.departure_window.start,
                body.departure_window.end,
            ),
            passenger_count=body.passenger_count,
            max_budget=budget,
            special_requirements=tuple(body.special_requirements),
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _response(mission)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/missions/{mission_id}", response_model=MissionResponse)
def get_mission(mission_id: UUID, session: SessionDep) -> MissionResponse:
    return _response(_service(session).get_mission(MissionId(mission_id)))


@router.post("/missions/{mission_id}/open", response_model=MissionResponse)
def open_mission(
    mission_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> MissionResponse:
    scope = f"POST:/v1/missions/{mission_id}/open"
    request_hash = canonical_request_hash({})
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _get_stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return MissionResponse.model_validate(stored.response_body)

        mission = _service(session).open_mission(
            mission_id=MissionId(mission_id),
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _response(mission)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
    return response
