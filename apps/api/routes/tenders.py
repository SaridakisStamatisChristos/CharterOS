from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from apps.api.routes.bookings import BookingResponse
from apps.api.routes.bookings import _response as booking_response
from apps.api.routes.quotes import (
    QuoteResponse,
    QuoteTermsRequest,
)
from apps.api.routes.quotes import (
    _response as quote_response,
)
from apps.api.routes.quotes import (
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
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["tenders"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


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


def _invitation_response(invitation: TenderInvitation) -> TenderInvitationResponse:
    return TenderInvitationResponse(
        id=invitation.id.value,
        tender_id=invitation.tender_id.value,
        operator_id=invitation.operator_id.value,
        rfq_id=invitation.rfq_id.value,
        status=invitation.status,
        invited_at=invitation.invited_at,
        responded_at=invitation.responded_at,
        last_quote_id=(
            invitation.last_quote_id.value if invitation.last_quote_id is not None else None
        ),
        best_and_final_quote_id=(
            invitation.best_and_final_quote_id.value
            if invitation.best_and_final_quote_id is not None
            else None
        ),
    )


def _correction_response(
    correction: TenderAdminCorrection,
) -> TenderAdminCorrectionResponse:
    return TenderAdminCorrectionResponse(
        id=correction.id.value,
        tender_id=correction.tender_id.value,
        actor_id=correction.actor_id.value,
        target_type=correction.target_type,
        target_id=correction.target_id.value,
        field_name=correction.field_name,
        original_value=cast(JsonValue, correction.original_value),
        replacement_value=cast(JsonValue, correction.replacement_value),
        reason=correction.reason,
        corrected_at=correction.corrected_at,
        causation_event_id=correction.causation_event_id.value,
    )


def _supplier_response(view: TenderSupplierView) -> TenderSupplierViewResponse:
    return TenderSupplierViewResponse(
        tender_id=view.tender.id.value,
        status=view.tender.status,
        sealed_bid=view.tender.sealed_bid,
        opens_at=view.tender.opens_at,
        deadline_at=view.tender.deadline_at,
        invitation=_invitation_response(view.invitation),
        own_quotes=[quote_response(quote) for quote in view.quotes],
    )


def _audit_response(audit: TenderAuditTrail) -> TenderAuditResponse:
    return TenderAuditResponse(
        tender=_tender_response(audit.tender),
        invitations=[_invitation_response(item) for item in audit.invitations],
        corrections=[_correction_response(item) for item in audit.corrections],
        events=[
            TenderAuditEventResponse(
                event_id=item.event_id,
                aggregate_type=item.aggregate_type,
                aggregate_id=item.aggregate_id,
                aggregate_version=item.aggregate_version,
                event_type=item.event_type,
                occurred_at=item.occurred_at,
                recorded_at=item.recorded_at,
                actor_id=item.actor_id,
                correlation_id=item.correlation_id,
                causation_id=item.causation_id,
                canonical_json=item.canonical_json,
            )
            for item in audit.events
        ],
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


@router.post(
    "/missions/{mission_id}/tenders",
    response_model=TenderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tender(
    mission_id: UUID,
    body: TenderCreateRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderResponse:
    scope = f"POST:/v1/missions/{mission_id}/tenders"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=TenderResponse,
            action=lambda: _tender_response(
                _service(session).create(
                    mission_id=MissionId(mission_id),
                    opens_at=body.opens_at,
                    deadline_at=body.deadline_at,
                    sealed_bid=body.sealed_bid,
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post("/tenders/{tender_id}/open", response_model=TenderResponse)
def open_tender(
    tender_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderResponse:
    scope = f"POST:/v1/tenders/{tender_id}/open"
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=TenderResponse,
            action=lambda: _tender_response(
                _service(session).open(
                    tender_id=TenderId(tender_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post(
    "/tenders/{tender_id}/invitations",
    response_model=TenderInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_supplier(
    tender_id: UUID,
    body: TenderInvitationRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderInvitationResponse:
    scope = f"POST:/v1/tenders/{tender_id}/invitations"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=TenderInvitationResponse,
            action=lambda: _invitation_response(
                _service(session).invite(
                    tender_id=TenderId(tender_id),
                    operator_id=OperatorId(body.operator_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post(
    "/tender-invitations/{invitation_id}/accept",
    response_model=TenderInvitationResponse,
)
def accept_invitation(
    invitation_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderInvitationResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/accept"
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=TenderInvitationResponse,
            action=lambda: _invitation_response(
                _service(session).accept_invitation(
                    invitation_id=TenderInvitationId(invitation_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post(
    "/tender-invitations/{invitation_id}/decline",
    response_model=TenderInvitationResponse,
)
def decline_invitation(
    invitation_id: UUID,
    body: TenderInvitationDeclineRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderInvitationResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/decline"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            response_type=TenderInvitationResponse,
            action=lambda: _invitation_response(
                _service(session).decline_invitation(
                    invitation_id=TenderInvitationId(invitation_id),
                    reason=body.reason,
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


def _submit_terms(
    *,
    session: Session,
    invitation_id: UUID,
    body: QuoteTermsRequest,
    correlation_id: CorrelationId,
    best_and_final: bool,
    quote_id: UUID | None = None,
    clock: Clock,
) -> QuoteResponse:
    aircraft_id, base_price, components, repositioning = quote_terms(body)
    service = _service(session)
    invitation = TenderInvitationId(invitation_id)
    now = clock.now()
    if quote_id is None:
        return quote_response(
            service.submit_bid(
                invitation_id=invitation,
                aircraft_id=aircraft_id,
                base_price=base_price,
                price_components=components,
                repositioning_cost=repositioning,
                inclusions=tuple(body.inclusions),
                exclusions=tuple(body.exclusions),
                cancellation_terms=body.cancellation_terms,
                payment_terms=body.payment_terms,
                valid_until=body.valid_until,
                now=now,
                correlation_id=correlation_id,
            )
        )
    current_quote_id = QuoteId(quote_id)
    if best_and_final:
        return quote_response(
            service.submit_best_and_final(
                invitation_id=invitation,
                quote_id=current_quote_id,
                aircraft_id=aircraft_id,
                base_price=base_price,
                price_components=components,
                repositioning_cost=repositioning,
                inclusions=tuple(body.inclusions),
                exclusions=tuple(body.exclusions),
                cancellation_terms=body.cancellation_terms,
                payment_terms=body.payment_terms,
                valid_until=body.valid_until,
                now=now,
                correlation_id=correlation_id,
            )
        )
    return quote_response(
        service.revise_bid(
            invitation_id=invitation,
            quote_id=current_quote_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=components,
            repositioning_cost=repositioning,
            inclusions=tuple(body.inclusions),
            exclusions=tuple(body.exclusions),
            cancellation_terms=body.cancellation_terms,
            payment_terms=body.payment_terms,
            valid_until=body.valid_until,
            now=now,
            correlation_id=correlation_id,
        )
    )


@router.post(
    "/tender-invitations/{invitation_id}/bids",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_bid(
    invitation_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> QuoteResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/bids"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=QuoteResponse,
            action=lambda: _submit_terms(
                session=session,
                invitation_id=invitation_id,
                body=body,
                correlation_id=correlation_id,
                best_and_final=False,
                clock=clock,
            ),
        )


@router.post(
    "/tender-invitations/{invitation_id}/bids/{quote_id}/revise",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def revise_bid(
    invitation_id: UUID,
    quote_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> QuoteResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/bids/{quote_id}/revise"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=QuoteResponse,
            action=lambda: _submit_terms(
                session=session,
                invitation_id=invitation_id,
                quote_id=quote_id,
                body=body,
                correlation_id=correlation_id,
                best_and_final=False,
                clock=clock,
            ),
        )


@router.post(
    "/tenders/{tender_id}/best-and-final",
    response_model=TenderResponse,
)
def request_best_and_final(
    tender_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderResponse:
    scope = f"POST:/v1/tenders/{tender_id}/best-and-final"
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=TenderResponse,
            action=lambda: _tender_response(
                _service(session).request_best_and_final(
                    tender_id=TenderId(tender_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post(
    "/tender-invitations/{invitation_id}/best-and-final/{quote_id}",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_best_and_final(
    invitation_id: UUID,
    quote_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> QuoteResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/best-and-final/{quote_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=QuoteResponse,
            action=lambda: _submit_terms(
                session=session,
                invitation_id=invitation_id,
                quote_id=quote_id,
                body=body,
                correlation_id=correlation_id,
                best_and_final=True,
                clock=clock,
            ),
        )


@router.post(
    "/tender-invitations/{invitation_id}/bids/{quote_id}/withdraw",
    response_model=QuoteResponse,
)
def withdraw_bid(
    invitation_id: UUID,
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> QuoteResponse:
    scope = f"POST:/v1/tender-invitations/{invitation_id}/bids/{quote_id}/withdraw"
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=QuoteResponse,
            action=lambda: quote_response(
                _service(session).withdraw_bid(
                    invitation_id=TenderInvitationId(invitation_id),
                    quote_id=QuoteId(quote_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post("/tenders/{tender_id}/close", response_model=TenderResponse)
def close_tender(
    tender_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderResponse:
    scope = f"POST:/v1/tenders/{tender_id}/close"
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=canonical_request_hash({}),
            success_status=status.HTTP_200_OK,
            response_type=TenderResponse,
            action=lambda: _tender_response(
                _service(session).close(
                    tender_id=TenderId(tender_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.post("/tenders/{tender_id}/award", response_model=TenderAwardResponse)
def award_tender(
    tender_id: UUID,
    body: TenderAwardRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderAwardResponse:
    scope = f"POST:/v1/tenders/{tender_id}/award"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))

    def action() -> TenderAwardResponse:
        tender, booking = _service(session).award(
            tender_id=TenderId(tender_id),
            quote_id=QuoteId(body.quote_id),
            now=clock.now(),
            correlation_id=correlation_id,
        )
        return TenderAwardResponse(
            tender=_tender_response(tender),
            booking=booking_response(booking),
        )

    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            response_type=TenderAwardResponse,
            action=action,
        )


@router.post(
    "/tenders/{tender_id}/admin-corrections",
    response_model=TenderAdminCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_correct(
    tender_id: UUID,
    body: TenderAdminCorrectionRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> TenderAdminCorrectionResponse:
    scope = f"POST:/v1/tenders/{tender_id}/admin-corrections"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            response_type=TenderAdminCorrectionResponse,
            action=lambda: _correction_response(
                _service(session).admin_correct(
                    tender_id=TenderId(tender_id),
                    actor_id=TenderActorId(body.actor_id),
                    target_type=body.target_type,
                    target_id=TypedId(body.target_id),
                    field_name=body.field_name,
                    original_value=body.original_value,
                    replacement_value=body.replacement_value,
                    reason=body.reason,
                    causation_event_id=EventId(body.causation_event_id),
                    now=clock.now(),
                    correlation_id=correlation_id,
                )
            ),
        )


@router.get("/tenders/{tender_id}", response_model=TenderDetailResponse)
def get_tender(tender_id: UUID, session: SessionDep) -> TenderDetailResponse:
    service = _service(session)
    tender = service.get(TenderId(tender_id))
    sealed_phase = tender.sealed_bid and tender.status in (
        TenderStatus.DRAFT,
        TenderStatus.OPEN,
        TenderStatus.BEST_AND_FINAL,
    )
    invitations = () if sealed_phase else service.list_invitations(tender.id)
    return TenderDetailResponse(
        tender=_tender_response(tender),
        invitations=[_invitation_response(item) for item in invitations],
    )


@router.get(
    "/tenders/{tender_id}/supplier-view",
    response_model=TenderSupplierViewResponse,
)
def supplier_view(
    tender_id: UUID,
    session: SessionDep,
    operator_id: Annotated[UUID, Header(alias="X-Operator-Id")],
    invitation_id: Annotated[UUID, Header(alias="X-Tender-Invitation-Id")],
) -> TenderSupplierViewResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        view = _service(session).supplier_view(
            tender_id=TenderId(tender_id),
            operator_id=OperatorId(operator_id),
            invitation_id=TenderInvitationId(invitation_id),
        )
    return _supplier_response(view)


@router.get("/tenders/{tender_id}/audit", response_model=TenderAuditResponse)
def audit_tender(tender_id: UUID, session: SessionDep) -> TenderAuditResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        audit = _service(session).audit_trail(TenderId(tender_id))
    return _audit_response(audit)
