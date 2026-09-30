from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from apps.api.routes.catalog import AircraftTypeCreate
from apps.api.routes.fleet import (
    AvailabilityCreate,
    AvailabilityResponse,
    TimelineResponse,
)
from apps.api.routes.fleet import (
    _availability_response as availability_response,
)
from apps.api.routes.fleet import _service as fleet_timeline_service
from apps.api.routes.fleet import _timeline_response as timeline_response
from apps.api.routes.quotes import QuoteResponse, QuoteTermsRequest
from apps.api.routes.quotes import _response as quote_response
from apps.api.routes.quotes import _terms as quote_terms
from apps.api.routes.repositioning import RepositionOptimizationResponse
from apps.api.routes.repositioning import _response as repositioning_response
from charteros.application.catalog import AircraftTypeSpec, CatalogService
from charteros.application.exceptions import EntityConflictError
from charteros.application.graph_queries import EmptyLegCandidate, GraphQueryService
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.operator_portal import (
    OperatorPortalService,
    PortalAircraft,
    PortalBooking,
    PortalRfq,
)
from charteros.application.quotes import QuoteService
from charteros.application.repositioning import RepositioningService
from charteros.application.rfqs import RfqService
from charteros.application.tenders import TenderService
from charteros.domain.aircraft import (
    AircraftId,
    AircraftStatus,
    AvailabilityRecordId,
)
from charteros.domain.airports import AirportId
from charteros.domain.bookings import BookingState
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.rfqs import RfqId, RfqStatus
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.tenders import TenderInvitationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyAircraftTypeRepository,
    SqlAlchemyAirportRepository,
    SqlAlchemyBookingRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyGraphQueryRepository,
    SqlAlchemyMatchingSnapshotRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorPortalRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRepositionOpportunityRepository,
    SqlAlchemyRfqRepository,
    SqlAlchemyTenderRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1/operator-portal", tags=["operator-portal"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
OperatorContext = Annotated[UUID, Header(alias="X-Operator-Id")]
TenderCapability = Annotated[UUID | None, Header(alias="X-Tender-Invitation-Id")]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
PageLimit = Annotated[int, Query(ge=1, le=100)]


class PortalAircraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration: str = Field(min_length=2, max_length=16)
    aircraft_type: AircraftTypeCreate
    seat_capacity: int = Field(gt=0, le=1000)
    cargo_capacity: Decimal = Field(ge=Decimal("0"))
    range_nm: int = Field(gt=0, le=50_000)
    home_base: UUID
    status: AircraftStatus = AircraftStatus.ACTIVE


class PortalAircraftResponse(BaseModel):
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


class PortalFleetPageResponse(BaseModel):
    operator_id: UUID
    returned_count: int
    next_cursor: UUID | None
    aircraft: list[PortalAircraftResponse]


class PortalRfqResponse(BaseModel):
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
    special_requirements: list[str]
    current_quote_id: UUID | None
    current_quote_status: str | None
    current_quote_revision: int | None
    tender_id: UUID | None
    tender_status: str | None
    tender_sealed_bid: bool | None
    tender_invitation_status: str | None
    tender_capability_required: bool


class PortalRfqPageResponse(BaseModel):
    operator_id: UUID
    returned_count: int
    next_cursor: UUID | None
    rfqs: list[PortalRfqResponse]


class PortalRfqDeclineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class PortalBookingResponse(BaseModel):
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


class PortalBookingPageResponse(BaseModel):
    operator_id: UUID
    returned_count: int
    next_cursor: UUID | None
    bookings: list[PortalBookingResponse]


class PortalCalendarResponse(BaseModel):
    operator_id: UUID
    window_start: datetime
    window_end: datetime
    returned_count: int
    next_cursor: UUID | None
    entries: list[PortalBookingResponse]


class PortalEmptyLegResponse(BaseModel):
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
    evidence_kind: str


class PortalEmptyLegVisibilityResponse(BaseModel):
    operator_id: UUID
    evidence_boundary: str
    projection_version: int
    graph_knowledge_cutoff: datetime
    structural_count: int
    structural_candidates: list[PortalEmptyLegResponse]
    optimization: RepositionOptimizationResponse | None


def _portal(session: Session) -> OperatorPortalService:
    return OperatorPortalService(SqlAlchemyOperatorPortalRepository(session))


def _catalog(session: Session) -> CatalogService:
    return CatalogService(
        organizations=SqlAlchemyOrganizationRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        aircraft_types=SqlAlchemyAircraftTypeRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _rfq_service(session: Session) -> RfqService:
    return RfqService(
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
    )


def _quote_service(session: Session) -> QuoteService:
    return QuoteService(
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
    )


def _tender_service(session: Session) -> TenderService:
    return TenderService(
        tenders=SqlAlchemyTenderRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _repositioning_service(session: Session) -> RepositioningService:
    return RepositioningService(
        graph=GraphQueryService(SqlAlchemyGraphQueryRepository(session)),
        airports=SqlAlchemyAirportRepository(session),
        snapshots=SqlAlchemyMatchingSnapshotRepository(session),
        opportunities=SqlAlchemyRepositionOpportunityRepository(session),
    )


def _aircraft_response(item: PortalAircraft) -> PortalAircraftResponse:
    return PortalAircraftResponse(
        id=item.id,
        version=item.version,
        operator_id=item.operator_id,
        registration=item.registration,
        aircraft_type_id=item.aircraft_type_id,
        manufacturer=item.manufacturer,
        model=item.model,
        category=item.category,
        seat_capacity=item.seat_capacity,
        cargo_capacity=item.cargo_capacity,
        range_nm=item.range_nm,
        home_base_id=item.home_base_id,
        home_base_icao=item.home_base_icao,
        status=item.status,
    )


def _rfq_response(item: PortalRfq) -> PortalRfqResponse:
    return PortalRfqResponse(
        id=item.id,
        version=item.version,
        operator_id=item.operator_id,
        status=item.status,
        created_at=item.created_at,
        sent_at=item.sent_at,
        response_deadline=item.response_deadline,
        acknowledged_at=item.acknowledged_at,
        declined_at=item.declined_at,
        expired_at=item.expired_at,
        decline_reason=item.decline_reason,
        mission_id=item.mission_id,
        mission_status=item.mission_status,
        origin_airport_id=item.origin_airport_id,
        origin_icao=item.origin_icao,
        destination_airport_id=item.destination_airport_id,
        destination_icao=item.destination_icao,
        departure_from=item.departure_from,
        departure_to=item.departure_to,
        passenger_count=item.passenger_count,
        special_requirements=list(item.special_requirements),
        current_quote_id=item.current_quote_id,
        current_quote_status=item.current_quote_status,
        current_quote_revision=item.current_quote_revision,
        tender_id=item.tender_id,
        tender_status=item.tender_status,
        tender_sealed_bid=item.tender_sealed_bid,
        tender_invitation_status=item.tender_invitation_status,
        tender_capability_required=item.tender_invitation_id is not None,
    )


def _booking_response(item: PortalBooking) -> PortalBookingResponse:
    return PortalBookingResponse(
        id=item.id,
        version=item.version,
        mission_id=item.mission_id,
        accepted_quote_id=item.accepted_quote_id,
        operator_id=item.operator_id,
        aircraft_id=item.aircraft_id,
        state=item.state,
        created_at=item.created_at,
        state_changed_at=item.state_changed_at,
        origin_airport_id=item.origin_airport_id,
        origin_icao=item.origin_icao,
        destination_airport_id=item.destination_airport_id,
        destination_icao=item.destination_icao,
        departure_from=item.departure_from,
        departure_to=item.departure_to,
        quote_currency=item.quote_currency,
    )


def _empty_leg_response(item: EmptyLegCandidate) -> PortalEmptyLegResponse:
    return PortalEmptyLegResponse(
        aircraft_id=item.aircraft_id,
        operator_id=item.operator_id,
        previous_booking_id=item.previous_booking_id,
        previous_mission_id=item.previous_mission_id,
        next_booking_id=item.next_booking_id,
        next_mission_id=item.next_mission_id,
        from_airport_id=item.from_airport_id,
        from_icao=item.from_icao,
        to_airport_id=item.to_airport_id,
        to_icao=item.to_icao,
        window_start=item.window_start,
        window_end=item.window_end,
        gap_minutes=item.gap_minutes,
        evidence_kind=item.evidence_kind,
    )


def _stored_response(
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


def _run_idempotent[ResponseT: BaseModel](
    *,
    session: Session,
    scope: str,
    key: str,
    request_hash: str,
    success_status: int,
    response_type: type[ResponseT],
    action: Callable[[], ResponseT],
) -> ResponseT:
    repository = SqlAlchemyIdempotencyRepository(session)
    repository.lock(scope, key)
    stored = _stored_response(
        repository,
        scope=scope,
        key=key,
        request_hash=request_hash,
    )
    if stored is not None:
        return response_type.model_validate(stored.response_body)
    response = action()
    repository.add(
        scope=scope,
        key=key,
        request_hash=request_hash,
        status_code=success_status,
        response_body=response.model_dump(mode="json"),
    )
    return response


@router.get("/fleet", response_model=PortalFleetPageResponse)
def list_fleet(
    session: SessionDep,
    operator_id: OperatorContext,
    limit: PageLimit = 50,
    cursor: UUID | None = None,
) -> PortalFleetPageResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = _portal(session).fleet(
            operator_id=OperatorId(operator_id),
            limit=limit,
            cursor=cursor,
        )
    return PortalFleetPageResponse(
        operator_id=operator_id,
        returned_count=len(page.items),
        next_cursor=page.next_cursor,
        aircraft=[_aircraft_response(item) for item in page.items],
    )


@router.get("/fleet/{aircraft_id}", response_model=PortalAircraftResponse)
def get_aircraft(
    aircraft_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
) -> PortalAircraftResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        item = _portal(session).aircraft(
            operator_id=OperatorId(operator_id),
            aircraft_id=aircraft_id,
        )
    return _aircraft_response(item)


@router.post(
    "/fleet",
    response_model=PortalAircraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_aircraft(
    body: PortalAircraftCreate,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> PortalAircraftResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/fleet"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))

    def action() -> PortalAircraftResponse:
        _portal(session).assert_operator(OperatorId(operator_id))
        spec = AircraftTypeSpec(
            manufacturer=body.aircraft_type.manufacturer,
            model=body.aircraft_type.model,
            category=body.aircraft_type.category,
            seats_min=body.aircraft_type.seats_min,
            seats_max=body.aircraft_type.seats_max,
            range_nm=body.aircraft_type.range_nm,
            runway_requirements=body.aircraft_type.runway_requirements,
            baggage_cargo_profile=body.aircraft_type.baggage_cargo_profile,
        )
        aircraft, _ = _catalog(session).create_aircraft(
            operator_id=OperatorId(operator_id),
            registration=body.registration,
            aircraft_type_spec=spec,
            seat_capacity=body.seat_capacity,
            cargo_capacity=body.cargo_capacity,
            range_nm=body.range_nm,
            home_base_id=AirportId(body.home_base),
            status=body.status,
            correlation_id=correlation_id,
        )
        return _aircraft_response(
            _portal(session).aircraft(
                operator_id=OperatorId(operator_id),
                aircraft_id=aircraft.id.value,
            )
        )

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=PortalAircraftResponse,
            action=action,
        )


@router.get(
    "/fleet/{aircraft_id}/availability",
    response_model=TimelineResponse,
)
def get_aircraft_availability(
    aircraft_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
    from_time: Annotated[datetime, Query(alias="from")],
    to_time: Annotated[datetime, Query(alias="to")],
    clock: ClockDep,
    known_as_of: datetime | None = None,
    at: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> TimelineResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _portal(session).aircraft(
            operator_id=OperatorId(operator_id),
            aircraft_id=aircraft_id,
        )
        timeline = fleet_timeline_service(session).get_timeline(
            aircraft_id=AircraftId(aircraft_id),
            from_time=from_time,
            to_time=to_time,
            known_as_of=known_as_of or clock.now(),
            state_at=at,
            limit=limit,
        )
    return timeline_response(timeline)


@router.post(
    "/fleet/{aircraft_id}/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_aircraft_availability(
    aircraft_id: UUID,
    body: AvailabilityCreate,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> AvailabilityResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/fleet/{aircraft_id}/availability"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))

    def action() -> AvailabilityResponse:
        _portal(session).aircraft(
            operator_id=OperatorId(operator_id),
            aircraft_id=aircraft_id,
        )
        record, aircraft_version = fleet_timeline_service(session).record_availability(
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
        return availability_response(record, aircraft_version=aircraft_version)

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=AvailabilityResponse,
            action=action,
        )


@router.get("/rfqs", response_model=PortalRfqPageResponse)
def rfq_inbox(
    session: SessionDep,
    operator_id: OperatorContext,
    rfq_status: Annotated[list[RfqStatus] | None, Query(alias="status")] = None,
    limit: PageLimit = 50,
    cursor: UUID | None = None,
) -> PortalRfqPageResponse:
    statuses = tuple(rfq_status or ())
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = _portal(session).rfq_inbox(
            operator_id=OperatorId(operator_id),
            statuses=statuses,
            limit=limit,
            cursor=cursor,
        )
    return PortalRfqPageResponse(
        operator_id=operator_id,
        returned_count=len(page.items),
        next_cursor=page.next_cursor,
        rfqs=[_rfq_response(item) for item in page.items],
    )


@router.get("/rfqs/{rfq_id}", response_model=PortalRfqResponse)
def get_rfq(
    rfq_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
    invitation_id: TenderCapability = None,
) -> PortalRfqResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        item = _portal(session).rfq(
            operator_id=OperatorId(operator_id),
            rfq_id=rfq_id,
            invitation_id=invitation_id,
            require_tender_capability=True,
        )
    return _rfq_response(item)


def _refresh_rfq(
    session: Session,
    *,
    operator_id: UUID,
    rfq_id: UUID,
    invitation_id: UUID | None,
) -> PortalRfqResponse:
    return _rfq_response(
        _portal(session).rfq(
            operator_id=OperatorId(operator_id),
            rfq_id=rfq_id,
            invitation_id=invitation_id,
            require_tender_capability=True,
        )
    )


@router.post("/rfqs/{rfq_id}/acknowledge", response_model=PortalRfqResponse)
def acknowledge_rfq(
    rfq_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
    invitation_id: TenderCapability = None,
) -> PortalRfqResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/rfqs/{rfq_id}/acknowledge"

    def action() -> PortalRfqResponse:
        item = _portal(session).rfq(
            operator_id=OperatorId(operator_id),
            rfq_id=rfq_id,
            invitation_id=invitation_id,
            require_tender_capability=True,
        )
        if item.tender_invitation_id is not None:
            _tender_service(session).accept_invitation(
                invitation_id=TenderInvitationId(item.tender_invitation_id),
                now=clock.now(),
                correlation_id=correlation_id,
            )
        else:
            _rfq_service(session).acknowledge(
                rfq_id=RfqId(rfq_id),
                now=clock.now(),
                correlation_id=correlation_id,
            )
        return _refresh_rfq(
            session,
            operator_id=operator_id,
            rfq_id=rfq_id,
            invitation_id=invitation_id,
        )

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=PortalRfqResponse,
            action=action,
        )


@router.post("/rfqs/{rfq_id}/decline", response_model=PortalRfqResponse)
def decline_rfq(
    rfq_id: UUID,
    body: PortalRfqDeclineRequest,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
    invitation_id: TenderCapability = None,
) -> PortalRfqResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/rfqs/{rfq_id}/decline"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))

    def action() -> PortalRfqResponse:
        item = _portal(session).rfq(
            operator_id=OperatorId(operator_id),
            rfq_id=rfq_id,
            invitation_id=invitation_id,
            require_tender_capability=True,
        )
        if item.tender_invitation_id is not None:
            _tender_service(session).decline_invitation(
                invitation_id=TenderInvitationId(item.tender_invitation_id),
                reason=body.reason,
                now=clock.now(),
                correlation_id=correlation_id,
            )
        else:
            _rfq_service(session).decline(
                rfq_id=RfqId(rfq_id),
                reason=body.reason,
                now=clock.now(),
                correlation_id=correlation_id,
            )
        return _refresh_rfq(
            session,
            operator_id=operator_id,
            rfq_id=rfq_id,
            invitation_id=invitation_id,
        )

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            response_type=PortalRfqResponse,
            action=action,
        )


def _submit_quote_for_context(
    *,
    session: Session,
    operator_id: UUID,
    rfq_id: UUID,
    invitation_id: UUID | None,
    body: QuoteTermsRequest,
    correlation_id: CorrelationId,

    clock: Clock,
) -> QuoteResponse:
    item = _portal(session).rfq(
        operator_id=OperatorId(operator_id),
        rfq_id=rfq_id,
        invitation_id=invitation_id,
        require_tender_capability=True,
    )
    aircraft_id, base_price, components, repositioning = quote_terms(body)
    if item.tender_invitation_id is not None:
        quote = _tender_service(session).submit_bid(
            invitation_id=TenderInvitationId(item.tender_invitation_id),
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=components,
            repositioning_cost=repositioning,
            inclusions=tuple(body.inclusions),
            exclusions=tuple(body.exclusions),
            cancellation_terms=body.cancellation_terms,
            payment_terms=body.payment_terms,
            valid_until=body.valid_until,
            now=clock.now(),
            correlation_id=correlation_id,
        )

    else:
        quote = _quote_service(session).submit(
            rfq_id=RfqId(rfq_id),
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=components,
            repositioning_cost=repositioning,
            inclusions=tuple(body.inclusions),
            exclusions=tuple(body.exclusions),
            cancellation_terms=body.cancellation_terms,
            payment_terms=body.payment_terms,
            valid_until=body.valid_until,
            now=clock.now(),
            correlation_id=correlation_id,
        )
    return quote_response(quote)


@router.post(
    "/rfqs/{rfq_id}/quotes",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_quote(
    rfq_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
    invitation_id: TenderCapability = None,
) -> QuoteResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/rfqs/{rfq_id}/quotes"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=QuoteResponse,
            action=lambda: _submit_quote_for_context(
                session=session,
                operator_id=operator_id,
                rfq_id=rfq_id,
                invitation_id=invitation_id,
                body=body,
                correlation_id=correlation_id,
                clock=clock,
            ),
        )


@router.get("/quotes/{quote_id}", response_model=QuoteResponse)
def get_quote(
    quote_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
    invitation_id: TenderCapability = None,
) -> QuoteResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _portal(session).quote_context(
            operator_id=OperatorId(operator_id),
            quote_id=quote_id,
            invitation_id=invitation_id,
        )
        quote = _quote_service(session).get(QuoteId(quote_id))
    return quote_response(quote)


def _revise_quote_for_context(
    *,
    session: Session,
    operator_id: UUID,
    quote_id: UUID,
    invitation_id: UUID | None,
    body: QuoteTermsRequest,
    correlation_id: CorrelationId,

    clock: Clock,
) -> QuoteResponse:
    context = _portal(session).quote_context(
        operator_id=OperatorId(operator_id),
        quote_id=quote_id,
        invitation_id=invitation_id,
    )
    aircraft_id, base_price, components, repositioning = quote_terms(body)
    if context.rfq.tender_invitation_id is not None:
        tender_service = _tender_service(session)
        typed_invitation = TenderInvitationId(context.rfq.tender_invitation_id)
        if context.rfq.tender_status == "best_and_final":
            quote = tender_service.submit_best_and_final(
                invitation_id=typed_invitation,
                quote_id=QuoteId(quote_id),
                aircraft_id=aircraft_id,
                base_price=base_price,
                price_components=components,
                repositioning_cost=repositioning,
                inclusions=tuple(body.inclusions),
                exclusions=tuple(body.exclusions),
                cancellation_terms=body.cancellation_terms,
                payment_terms=body.payment_terms,
                valid_until=body.valid_until,
                now=clock.now(),
                correlation_id=correlation_id,
            )
        else:
            quote = tender_service.revise_bid(
                invitation_id=typed_invitation,
                quote_id=QuoteId(quote_id),
                aircraft_id=aircraft_id,
                base_price=base_price,
                price_components=components,
                repositioning_cost=repositioning,
                inclusions=tuple(body.inclusions),
                exclusions=tuple(body.exclusions),
                cancellation_terms=body.cancellation_terms,
                payment_terms=body.payment_terms,
                valid_until=body.valid_until,
                now=clock.now(),
                correlation_id=correlation_id,
            )
    else:
        quote = _quote_service(session).revise(
            quote_id=QuoteId(quote_id),
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=components,
            repositioning_cost=repositioning,
            inclusions=tuple(body.inclusions),
            exclusions=tuple(body.exclusions),
            cancellation_terms=body.cancellation_terms,
            payment_terms=body.payment_terms,
            valid_until=body.valid_until,
            now=clock.now(),
            correlation_id=correlation_id,
        )
    return quote_response(quote)


@router.post(
    "/quotes/{quote_id}/revise",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def revise_quote(
    quote_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
    invitation_id: TenderCapability = None,
) -> QuoteResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/quotes/{quote_id}/revise"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=QuoteResponse,
            action=lambda: _revise_quote_for_context(
                session=session,
                operator_id=operator_id,
                quote_id=quote_id,
                invitation_id=invitation_id,
                body=body,
                correlation_id=correlation_id,
                clock=clock,
            ),
        )


@router.post("/quotes/{quote_id}/withdraw", response_model=QuoteResponse)
def withdraw_quote(
    quote_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
    invitation_id: TenderCapability = None,
) -> QuoteResponse:
    scope = f"POST:/v1/operator-portal/{operator_id}/quotes/{quote_id}/withdraw"

    def action() -> QuoteResponse:
        context = _portal(session).quote_context(
            operator_id=OperatorId(operator_id),
            quote_id=quote_id,
            invitation_id=invitation_id,
        )
        if context.rfq.tender_invitation_id is not None:
            quote = _tender_service(session).withdraw_bid(
                invitation_id=TenderInvitationId(context.rfq.tender_invitation_id),
                quote_id=QuoteId(quote_id),
                now=clock.now(),
                correlation_id=correlation_id,
            )
        else:
            quote = _quote_service(session).withdraw(
                quote_id=QuoteId(quote_id),
                now=clock.now(),
                correlation_id=correlation_id,
            )
        return quote_response(quote)

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=QuoteResponse,
            action=action,
        )


@router.get("/calendar", response_model=PortalCalendarResponse)
def mission_calendar(
    session: SessionDep,
    operator_id: OperatorContext,
    window_start: datetime,
    window_end: datetime,
    booking_state: Annotated[list[BookingState] | None, Query(alias="state")] = None,
    limit: PageLimit = 100,
    cursor: UUID | None = None,
) -> PortalCalendarResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = _portal(session).mission_calendar(
            operator_id=OperatorId(operator_id),
            window_start=window_start,
            window_end=window_end,
            states=tuple(booking_state or ()),
            limit=limit,
            cursor=cursor,
        )
    return PortalCalendarResponse(
        operator_id=operator_id,
        window_start=window_start,
        window_end=window_end,
        returned_count=len(page.items),
        next_cursor=page.next_cursor,
        entries=[_booking_response(item) for item in page.items],
    )


@router.get("/empty-legs", response_model=PortalEmptyLegVisibilityResponse)
def empty_leg_visibility(
    session: SessionDep,
    operator_id: OperatorContext,
    window_start: datetime,
    window_end: datetime,
    evaluated_at: datetime,
    mode: Literal["structural", "optimized", "both"] = "both",
    empty_leg_limit: Annotated[int, Query(ge=1, le=100)] = 100,
    opportunity_limit: Annotated[int, Query(ge=1, le=2000)] = 2000,
) -> PortalEmptyLegVisibilityResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _portal(session).assert_operator(OperatorId(operator_id))
        graph = GraphQueryService(SqlAlchemyGraphQueryRepository(session))
        structural = graph.empty_leg_candidates(
            window_start=window_start,
            window_end=window_end,
            limit=empty_leg_limit,
            operator_id=operator_id,
        )
        projection_version = graph.projection_version
        graph_knowledge_cutoff = graph.knowledge_cutoff
        optimized: RepositionOptimizationResponse | None = None
        if mode in ("optimized", "both"):
            optimized = repositioning_response(
                _repositioning_service(session).optimize(
                    window_start=window_start,
                    window_end=window_end,
                    evaluated_at=evaluated_at,
                    empty_leg_limit=empty_leg_limit,
                    opportunity_limit=opportunity_limit,
                    operator_id=OperatorId(operator_id),
                )
            )
    return PortalEmptyLegVisibilityResponse(
        operator_id=operator_id,
        evidence_boundary=(
            "PR16 structural candidates are not profitability claims; "
            "PR18 optimization is deterministic, currency-scoped recommendation evidence"
        ),
        projection_version=projection_version,
        graph_knowledge_cutoff=graph_knowledge_cutoff,
        structural_count=len(structural),
        structural_candidates=(
            [_empty_leg_response(item) for item in structural]
            if mode in ("structural", "both")
            else []
        ),
        optimization=optimized,
    )


@router.get("/bookings", response_model=PortalBookingPageResponse)
def list_bookings(
    session: SessionDep,
    operator_id: OperatorContext,
    booking_state: Annotated[list[BookingState] | None, Query(alias="state")] = None,
    limit: PageLimit = 50,
    cursor: UUID | None = None,
) -> PortalBookingPageResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = _portal(session).bookings(
            operator_id=OperatorId(operator_id),
            states=tuple(booking_state or ()),
            limit=limit,
            cursor=cursor,
        )
    return PortalBookingPageResponse(
        operator_id=operator_id,
        returned_count=len(page.items),
        next_cursor=page.next_cursor,
        bookings=[_booking_response(item) for item in page.items],
    )


@router.get("/bookings/{booking_id}", response_model=PortalBookingResponse)
def get_booking(
    booking_id: UUID,
    session: SessionDep,
    operator_id: OperatorContext,
) -> PortalBookingResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        item = _portal(session).booking(
            operator_id=OperatorId(operator_id),
            booking_id=booking_id,
        )
    return _booking_response(item)
