from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_correlation_id, get_session
from apps.api.routes.bookings import BookingResponse, _response as booking_response
from apps.api.routes.quotes import (
    QuoteResponse,
    QuoteTermsRequest,
    _response as quote_response,
    _terms as quote_terms,
)
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.tenders import TenderAuditTrail, TenderService, TenderSupplierView
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId
from charteros.domain.tenders import (
    Tender,
    TenderActorId,
    TenderAdminCorrection,
    TenderId,
    TenderInvitation,
    TenderInvitationId,
    TenderInvitationStatus,
    TenderStatus,
)
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyBookingRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
    SqlAlchemyTenderRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository

router = APIRouter(prefix="/v1", tags=["tenders"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class TenderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opens_at: datetime
    deadline_at: datetime
    sealed_bid: bool = True


class TenderInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_id: UUID


class TenderInvitationDeclineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class TenderAwardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID


class TenderAdminCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: UUID
    target_type: str = Field(min_length=1, max_length=64)
    target_id: UUID
    field_name: str = Field(min_length=1, max_length=128)
    original_value: JsonValue
    replacement_value: JsonValue
    reason: str = Field(min_length=1, max_length=1000)
    causation_event_id: UUID


class TenderResponse(BaseModel):
    id: UUID
    version: int
    mission_id: UUID
    status: TenderStatus
    sealed_bid: bool
    opens_at: datetime
    deadline_at: datetime
    created_at: datetime
    opened_at: datetime | None
    best_and_final_requested_at: datetime | None
    closed_at: datetime | None
    awarded_quote_id: UUID | None
    booking_id: UUID | None
    awarded_at: datetime | None


class TenderInvitationResponse(BaseModel):
    id: UUID
    tender_id: UUID
    operator_id: UUID
    rfq_id: UUID
    status: TenderInvitationStatus
    invited_at: datetime
    responded_at: datetime | None
    last_quote_id: UUID | None
    best_and_final_quote_id: UUID | None


class TenderDetailResponse(BaseModel):
    tender: TenderResponse
    invitations: list[TenderInvitationResponse]


class TenderSupplierViewResponse(BaseModel):
    tender_id: UUID
    status: TenderStatus
    sealed_bid: bool
    opens_at: datetime
    deadline_at: datetime
    invitation: TenderInvitationResponse
    own_quotes: list[QuoteResponse]


class TenderAwardResponse(BaseModel):
    tender: TenderResponse
    booking: BookingResponse


class TenderAdminCorrectionResponse(BaseModel):
    id: UUID
    tender_id: UUID
    actor_id: UUID
    target_type: str
    target_id: UUID
    field_name: str
    original_value: JsonValue
    replacement_value: JsonValue
    reason: str
    corrected_at: datetime
    causation_event_id: UUID | None


class TenderAuditEventResponse(BaseModel):
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    canonical_json: str


class TenderAuditResponse(BaseModel):
    tender: TenderResponse
    invitations: list[TenderInvitationResponse]
    corrections: list[TenderAdminCorrectionResponse]
    events: list[TenderAuditEventResponse]


def _service(session: Session) -> TenderService:
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


def _tender_response(tender: Tender) -> TenderResponse:
    return TenderResponse(
        id=tender.id.value,
        version=tender.version,
        mission_id=tender.mission_id.value,
        status=tender.status,
        sealed_bid=tender.sealed_bid,
        opens_at=tender.opens_at,
        deadline_at=tender.deadline_at,
        created_at=tender.created_at,
        opened_at=tender.opened_at,
        best_and_final_requested_at=tender.best_and_final_requested_at,
        closed_at=tender.closed_at,
        awarded_quote_id=(
            tender.awarded_quote_id.value if tender.awarded_quote_id is not None else None
        ),
        booking_id=tender.booking_id.value if tender.booking_id is not None else None,
        awarded_at=tender.awarded_at,
    )
