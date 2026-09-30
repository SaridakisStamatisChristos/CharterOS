from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_session
from charteros.application.graph_queries import GraphQueryService, HistoricalPosition
from charteros.application.matching import MatchingService
from charteros.application.tender_visibility import TenderVisibilityPolicy
from charteros.domain.missions import MissionId
from charteros.domain.quotes import QuoteId
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyAirportRepository
from charteros.infrastructure.db.repositories.graph_queries import (
    SqlAlchemyGraphQueryRepository,
)
from charteros.infrastructure.db.repositories.matching import SqlAlchemyMatchingSnapshotRepository
from charteros.infrastructure.db.repositories.missions import SqlAlchemyMissionRepository
from charteros.infrastructure.db.repositories.quotes import SqlAlchemyQuoteRepository
from charteros.infrastructure.db.repositories.tenders import SqlAlchemyTenderRepository
from charteros.matching import MatchReasonCode
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1/graph", tags=["graph-queries"])
ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
KnownAsOf = Annotated[
    datetime | None,
    Query(description="UTC knowledge-time cutoff; defaults to request time"),
]
ResultLimit = Annotated[int, Query(ge=1, le=100)]


class HistoricalPositionResponse(BaseModel):
    aircraft_id: UUID
    position_id: UUID
    airport_id: UUID | None
    latitude: Decimal
    longitude: Decimal
    event_time: datetime
    knowledge_time: datetime
    source: str
    provenance: dict[str, object]


class NearbyAircraftItemResponse(BaseModel):
    aircraft_id: UUID
    distance_nm: Decimal
    position: HistoricalPositionResponse


class NearbyAircraftResponse(BaseModel):
    projection_version: int
    airport_id: UUID
    event_time: datetime
    known_as_of: datetime
    radius_nm: Decimal
    returned_count: int
    aircraft: list[NearbyAircraftItemResponse]


class FeasibleAircraftItemResponse(BaseModel):
    rank: int
    aircraft_id: UUID
    operator_id: UUID
    aircraft_type_id: UUID
    reason_codes: list[MatchReasonCode]
    route_distance_nm: Decimal
    reposition_distance_nm: Decimal
    estimated_operating_cost_minor: int
    estimated_operating_cost_currency: str


class FeasibleAircraftResponse(BaseModel):
    projection_version: int
    mission_id: UUID
    policy_version: str
    known_as_of: datetime
    candidate_count: int
    feasible_count: int
    returned_count: int
    aircraft: list[FeasibleAircraftItemResponse]


class OperatorRouteResponse(BaseModel):
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


class OperatorRouteHistoryResponse(BaseModel):
    projection_version: int
    operator_id: UUID
    returned_count: int
    routes: list[OperatorRouteResponse]


class QuoteRevisionResponse(BaseModel):
    quote_id: UUID
    revision_number: int
    status: str
    supersedes_quote_id: UUID | None


class QuoteEventResponse(BaseModel):
    event_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, object]


class QuoteHistoryResponse(BaseModel):
    projection_version: int
    quote_id: UUID
    rfq_id: UUID
    revisions: list[QuoteRevisionResponse]
    events: list[QuoteEventResponse]


class EmptyLegCandidateResponse(BaseModel):
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


class EmptyLegCandidatesResponse(BaseModel):
    projection_version: int
    query_window_start: datetime
    query_window_end: datetime
    returned_count: int
    candidates: list[EmptyLegCandidateResponse]


class BookingFlightLineageResponse(BaseModel):
    projection_version: int
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
    flight_entity_id: UUID | None
    lineage_status: str


def _service(session: Session) -> GraphQueryService:
    return GraphQueryService(SqlAlchemyGraphQueryRepository(session))


def _visibility_policy(session: Session) -> TenderVisibilityPolicy:
    return TenderVisibilityPolicy(
        tenders=SqlAlchemyTenderRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
    )


def _position_response(item: HistoricalPosition) -> HistoricalPositionResponse:
    return HistoricalPositionResponse(
        aircraft_id=item.aircraft_id,
        position_id=item.position_id,
        airport_id=item.airport_id,
        latitude=item.latitude,
        longitude=item.longitude,
        event_time=item.event_time,
        knowledge_time=item.knowledge_time,
        source=item.source,
        provenance=item.provenance,
    )


@router.get(
    "/aircraft/near-airport",
    response_model=NearbyAircraftResponse,
)
def aircraft_near_airport(
    session: SessionDep,
    airport_id: UUID,
    at: Annotated[datetime, Query(description="UTC event-time cutoff")],
    clock: ClockDep,
    known_as_of: KnownAsOf = None,
    radius_nm: Annotated[Decimal, Query(gt=0, le=5000)] = Decimal("100"),
    limit: ResultLimit = 20,
) -> NearbyAircraftResponse:
    knowledge_cutoff = known_as_of or clock.now()
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        service = _service(session)
        items = service.nearby_aircraft(
            airport_id=airport_id,
            event_time=at,
            known_as_of=knowledge_cutoff,
            radius_nm=radius_nm,
            limit=limit,
        )
        version = service.projection_version
    return NearbyAircraftResponse(
        projection_version=version,
        airport_id=airport_id,
        event_time=at,
        known_as_of=knowledge_cutoff,
        radius_nm=radius_nm,
        returned_count=len(items),
        aircraft=[
            NearbyAircraftItemResponse(
                aircraft_id=item.position.aircraft_id,
                distance_nm=item.distance_nm,
                position=_position_response(item.position),
            )
            for item in items
        ],
    )


@router.get(
    "/aircraft/{aircraft_id}/historical-position",
    response_model=HistoricalPositionResponse,
)
def historical_aircraft_position(
    aircraft_id: UUID,
    session: SessionDep,
    at: Annotated[datetime, Query(description="UTC event-time cutoff")],
    clock: ClockDep,
    known_as_of: KnownAsOf = None,
) -> HistoricalPositionResponse:
    knowledge_cutoff = known_as_of or clock.now()
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        item = _service(session).historical_position(
            aircraft_id=aircraft_id,
            event_time=at,
            known_as_of=knowledge_cutoff,
        )
    return _position_response(item)


@router.get(
    "/missions/{mission_id}/feasible-aircraft",
    response_model=FeasibleAircraftResponse,
)
def feasible_aircraft_for_mission(
    mission_id: UUID,
    session: SessionDep,
    clock: ClockDep,
    known_as_of: KnownAsOf = None,
    limit: ResultLimit = 20,
) -> FeasibleAircraftResponse:
    cutoff = known_as_of or clock.now()
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        graph = _service(session)
        version = graph.assert_projected_mission(mission_id)
        decision = MatchingService(
            missions=SqlAlchemyMissionRepository(session),
            airports=SqlAlchemyAirportRepository(session),
            snapshots=SqlAlchemyMatchingSnapshotRepository(session),
        ).match_mission(mission_id=MissionId(mission_id), known_as_of=cutoff)
    matches = decision.matches[:limit]
    return FeasibleAircraftResponse(
        projection_version=version,
        mission_id=mission_id,
        policy_version=decision.policy_version,
        known_as_of=decision.known_as_of,
        candidate_count=decision.candidate_count,
        feasible_count=decision.feasible_count,
        returned_count=len(matches),
        aircraft=[
            FeasibleAircraftItemResponse(
                rank=item.rank,
                aircraft_id=item.draft.aircraft_id.value,
                operator_id=item.draft.operator_id.value,
                aircraft_type_id=item.draft.aircraft_type_id.value,
                reason_codes=list(item.draft.reason_codes),
                route_distance_nm=_nm(item.draft.route_distance_tenths_nm),
                reposition_distance_nm=_nm(item.draft.reposition_distance_tenths_nm),
                estimated_operating_cost_minor=item.draft.estimated_operating_cost.amount_minor,
                estimated_operating_cost_currency=str(item.draft.estimated_operating_cost.currency),
            )
            for item in matches
        ],
    )


@router.get(
    "/operators/{operator_id}/route-history",
    response_model=OperatorRouteHistoryResponse,
)
def operator_route_history(
    operator_id: UUID,
    session: SessionDep,
    limit: ResultLimit = 50,
) -> OperatorRouteHistoryResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        service = _service(session)
        routes = service.operator_route_history(operator_id=operator_id, limit=limit)
        version = service.projection_version
    return OperatorRouteHistoryResponse(
        projection_version=version,
        operator_id=operator_id,
        returned_count=len(routes),
        routes=[
            OperatorRouteResponse(
                booking_id=item.booking_id,
                mission_id=item.mission_id,
                aircraft_id=item.aircraft_id,
                accepted_quote_id=item.accepted_quote_id,
                origin_airport_id=item.origin_airport_id,
                origin_icao=item.origin_icao,
                destination_airport_id=item.destination_airport_id,
                destination_icao=item.destination_icao,
                departure_from=item.departure_from,
                departure_to=item.departure_to,
                booking_state=item.booking_state,
            )
            for item in routes
        ],
    )


@router.get(
    "/quotes/{quote_id}/history",
    response_model=QuoteHistoryResponse,
)
def quote_history(
    quote_id: UUID,
    session: SessionDep,
) -> QuoteHistoryResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _visibility_policy(session).get_visible_quote(QuoteId(quote_id))
        service = _service(session)
        item = service.quote_history(quote_id=quote_id)
        version = service.projection_version
    return QuoteHistoryResponse(
        projection_version=version,
        quote_id=item.quote_id,
        rfq_id=item.rfq_id,
        revisions=[
            QuoteRevisionResponse(
                quote_id=revision.quote_id,
                revision_number=revision.revision_number,
                status=revision.status,
                supersedes_quote_id=revision.supersedes_quote_id,
            )
            for revision in item.revisions
        ],
        events=[
            QuoteEventResponse(
                event_id=event.event_id,
                aggregate_version=event.aggregate_version,
                event_type=event.event_type,
                event_version=event.event_version,
                occurred_at=event.occurred_at,
                recorded_at=event.recorded_at,
                payload=event.payload,
            )
            for event in item.events
        ],
    )


@router.get(
    "/empty-leg-candidates",
    response_model=EmptyLegCandidatesResponse,
)
def empty_leg_candidates(
    session: SessionDep,
    window_start: datetime,
    window_end: datetime,
    limit: ResultLimit = 50,
) -> EmptyLegCandidatesResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        service = _service(session)
        items = service.empty_leg_candidates(
            window_start=window_start,
            window_end=window_end,
            limit=limit,
        )
        version = service.projection_version
    return EmptyLegCandidatesResponse(
        projection_version=version,
        query_window_start=window_start,
        query_window_end=window_end,
        returned_count=len(items),
        candidates=[
            EmptyLegCandidateResponse(
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
            for item in items
        ],
    )


@router.get(
    "/bookings/{booking_id}/flight-lineage",
    response_model=BookingFlightLineageResponse,
)
def booking_flight_lineage(
    booking_id: UUID,
    session: SessionDep,
) -> BookingFlightLineageResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        service = _service(session)
        item = service.booking_flight_lineage(booking_id=booking_id)
        version = service.projection_version
    return BookingFlightLineageResponse(
        projection_version=version,
        booking_id=item.booking_id,
        mission_id=item.mission_id,
        accepted_quote_id=item.accepted_quote_id,
        operator_id=item.operator_id,
        aircraft_id=item.aircraft_id,
        origin_airport_id=item.origin_airport_id,
        origin_icao=item.origin_icao,
        destination_airport_id=item.destination_airport_id,
        destination_icao=item.destination_icao,
        departure_from=item.departure_from,
        departure_to=item.departure_to,
        booking_state=item.booking_state,
        flight_entity_id=item.flight_entity_id,
        lineage_status=item.lineage_status,
    )


def _nm(tenths: int) -> Decimal:
    return (Decimal(tenths) / Decimal(10)).quantize(Decimal("0.1"))
