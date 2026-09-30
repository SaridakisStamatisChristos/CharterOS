from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.shared.clock import Clock
from charteros.application.exceptions import EntityConflictError
from charteros.application.fleet import AircraftTimeline, FleetTimelineService
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.aircraft import (
    AircraftAvailabilityRecord,
    AircraftId,
    AircraftPositionObservation,
    AvailabilityRecordId,
    AvailabilityStatus,
)
from charteros.domain.airports import AirportId
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories.catalog import (
    SqlAlchemyAirportRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyIdempotencyRepository,
)
from charteros.infrastructure.db.repositories.fleet import (
    SqlAlchemyFleetAircraftRepository,
    SqlAlchemyFleetTimelineRepository,
)

router = APIRouter(prefix="/v1", tags=["fleet-timeline"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport_id: UUID | None = None
    lat: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    lon: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    event_time: datetime
    source: str = Field(min_length=1, max_length=64)
    provenance: dict[str, object] = Field(default_factory=dict)


class PositionResponse(BaseModel):
    id: UUID
    aircraft_id: UUID
    airport_id: UUID | None
    lat: Decimal | None
    lon: Decimal | None
    event_time: datetime
    recorded_at: datetime
    source: str
    provenance: dict[str, object]
    aircraft_version: int | None = None


class AvailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_from: datetime
    valid_to: datetime
    status: AvailabilityStatus
    source: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=500)
    provenance: dict[str, object] = Field(default_factory=dict)
    supersedes_id: UUID | None = None


class AvailabilityResponse(BaseModel):
    id: UUID
    aircraft_id: UUID
    valid_from: datetime
    valid_to: datetime
    status: AvailabilityStatus
    recorded_at: datetime
    source: str
    reason: str | None
    provenance: dict[str, object]
    supersedes_id: UUID | None
    superseded_at: datetime | None
    authoritative_as_of: bool | None = None
    aircraft_version: int | None = None


class FleetStateResponse(BaseModel):
    event_time: datetime
    position: PositionResponse | None
    availability: AvailabilityResponse | None


class TimelineResponse(BaseModel):
    aircraft_id: UUID
    from_time: datetime
    to_time: datetime
    known_as_of: datetime
    positions: list[PositionResponse]
    availability: list[AvailabilityResponse]
    positions_truncated: bool
    availability_truncated: bool
    state: FleetStateResponse | None


def _service(session: Session) -> FleetTimelineService:
    return FleetTimelineService(
        aircraft=SqlAlchemyFleetAircraftRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        timeline=SqlAlchemyFleetTimelineRepository(session),
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


def _position_response(
    observation: AircraftPositionObservation, *, aircraft_version: int | None = None
) -> PositionResponse:
    return PositionResponse(
        id=observation.id.value,
        aircraft_id=observation.aircraft_id.value,
        airport_id=observation.airport_id.value if observation.airport_id else None,
        lat=observation.latitude,
        lon=observation.longitude,
        event_time=observation.event_time,
        recorded_at=observation.recorded_at,
        source=observation.source,
        provenance=dict(observation.provenance),
        aircraft_version=aircraft_version,
    )


def _availability_response(
    record: AircraftAvailabilityRecord,
    *,
    known_as_of: datetime | None = None,
    aircraft_version: int | None = None,
) -> AvailabilityResponse:
    return AvailabilityResponse(
        id=record.id.value,
        aircraft_id=record.aircraft_id.value,
        valid_from=record.interval.start,
        valid_to=record.interval.end,
        status=record.status,
        recorded_at=record.recorded_at,
        source=record.source,
        reason=record.reason,
        provenance=dict(record.provenance),
        supersedes_id=record.supersedes_id.value if record.supersedes_id else None,
        superseded_at=record.superseded_at,
        authoritative_as_of=(
            record.is_authoritative_as_of(known_as_of) if known_as_of is not None else None
        ),
        aircraft_version=aircraft_version,
    )


def _timeline_response(timeline: AircraftTimeline) -> TimelineResponse:
    state = None
    if timeline.state is not None:
        state = FleetStateResponse(
            event_time=timeline.state.event_time,
            position=(
                _position_response(timeline.state.position)
                if timeline.state.position is not None
                else None
            ),
            availability=(
                _availability_response(
                    timeline.state.availability,
                    known_as_of=timeline.known_as_of,
                )
                if timeline.state.availability is not None
                else None
            ),
        )
    return TimelineResponse(
        aircraft_id=timeline.aircraft_id.value,
        from_time=timeline.window.start,
        to_time=timeline.window.end,
        known_as_of=timeline.known_as_of,
        positions=[_position_response(item) for item in timeline.positions],
        availability=[
            _availability_response(item, known_as_of=timeline.known_as_of)
            for item in timeline.availability
        ],
        positions_truncated=timeline.positions_truncated,
        availability_truncated=timeline.availability_truncated,
        state=state,
    )


@router.post(
    "/aircraft/{aircraft_id}/positions",
    response_model=PositionResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_position(
    aircraft_id: UUID,
    body: PositionCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> PositionResponse:
    scope = f"POST:/v1/aircraft/{aircraft_id}/positions"
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
            return PositionResponse.model_validate(stored.response_body)

        observation, aircraft_version = _service(session).record_position(
            aircraft_id=AircraftId(aircraft_id),
            airport_id=AirportId(body.airport_id) if body.airport_id is not None else None,
            latitude=body.lat,
            longitude=body.lon,
            event_time=body.event_time,
            source=body.source,
            provenance=body.provenance,
            correlation_id=correlation_id,
        )
        response = _position_response(observation, aircraft_version=aircraft_version)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/aircraft/{aircraft_id}/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_availability(
    aircraft_id: UUID,
    body: AvailabilityCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> AvailabilityResponse:
    scope = f"POST:/v1/aircraft/{aircraft_id}/availability"
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
            return AvailabilityResponse.model_validate(stored.response_body)

        record, aircraft_version = _service(session).record_availability(
            aircraft_id=AircraftId(aircraft_id),
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            status=body.status,
            source=body.source,
            reason=body.reason,
            provenance=body.provenance,
            supersedes_id=(
                AvailabilityRecordId(body.supersedes_id) if body.supersedes_id is not None else None
            ),
            correlation_id=correlation_id,
        )
        response = _availability_response(record, aircraft_version=aircraft_version)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/aircraft/{aircraft_id}/timeline", response_model=TimelineResponse)
def get_timeline(
    aircraft_id: UUID,
    session: SessionDep,
    from_time: Annotated[datetime, Query(alias="from")],
    to_time: Annotated[datetime, Query(alias="to")],
    known_as_of: Annotated[datetime | None, Query()] = None,
    at: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,

    clock: Clock = Depends(get_clock),) -> TimelineResponse:
    timeline = _service(session).get_timeline(
        aircraft_id=AircraftId(aircraft_id),
        from_time=from_time,
        to_time=to_time,
        known_as_of=known_as_of or clock.now(),
        state_at=at,
        limit=limit,
    )
    return _timeline_response(timeline)
