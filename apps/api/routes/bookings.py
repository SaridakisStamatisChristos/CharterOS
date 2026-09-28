from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.dependencies import get_correlation_id, get_session
from charteros.application.bookings import BookingService
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.bookings import Booking, BookingId, BookingState
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyBookingRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository

router = APIRouter(prefix="/v1", tags=["bookings"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class BookingResponse(BaseModel):
    id: UUID
    version: int
    mission_id: UUID
    accepted_quote_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    state: BookingState
    created_at: datetime


def _service(session: Session) -> BookingService:
    return BookingService(
        bookings=SqlAlchemyBookingRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _response(booking: Booking) -> BookingResponse:
    return BookingResponse(
        id=booking.id.value,
        version=booking.version,
        mission_id=booking.mission_id.value,
        accepted_quote_id=booking.accepted_quote_id.value,
        operator_id=booking.operator_id.value,
        aircraft_id=booking.aircraft_id.value,
        state=booking.state,
        created_at=booking.created_at,
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


def _run_idempotent(
    *,
    session: Session,
    scope: str,
    key: str,
    request_hash: str,
    success_status: int,
    action: Callable[[], Booking],
) -> BookingResponse:
    idempotency = SqlAlchemyIdempotencyRepository(session)
    idempotency.lock(scope, key)
    stored = _stored_response(
        idempotency,
        scope=scope,
        key=key,
        request_hash=request_hash,
    )
    if stored is not None:
        return BookingResponse.model_validate(stored.response_body)

    response = _response(action())
    idempotency.add(
        scope=scope,
        key=key,
        request_hash=request_hash,
        status_code=success_status,
        response_body=response.model_dump(mode="json"),
    )
    return response


@router.post(
    "/quotes/{quote_id}/accept",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def accept_quote(
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> BookingResponse:
    scope = f"POST:/v1/quotes/{quote_id}/accept"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            action=lambda: _service(session).accept_quote(
                quote_id=QuoteId(quote_id),
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            ),
        )


@router.get("/bookings/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: UUID, session: SessionDep) -> BookingResponse:
    return _response(_service(session).get_booking(BookingId(booking_id)))
