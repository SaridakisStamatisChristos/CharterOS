from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_correlation_id, get_session
from charteros.application.disruptions import DisruptionPartyContext, DisruptionService
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import BookingId, BookingState
from charteros.domain.disruptions import (
    Disruption,
    DisruptionBuyerDecision,
    DisruptionBuyerDecisionValue,
    DisruptionCommercialChange,
    DisruptionCommercialChangeId,
    DisruptionId,
    DisruptionProposalId,
    DisruptionStatus,
    DisruptionType,
    ReplacementProposal,
)
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyBookingRepository,
    SqlAlchemyDisruptionRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
)
from charteros.infrastructure.db.repositories.fleet import SqlAlchemyFleetTimelineRepository

router = APIRouter(prefix="/v1", tags=["disruptions"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
BuyerIdOptional = Annotated[UUID | None, Header(alias="X-Buyer-Id")]
OperatorIdOptional = Annotated[UUID | None, Header(alias="X-Operator-Id")]
OperatorIdDep = Annotated[UUID, Header(alias="X-Operator-Id")]
BuyerIdDep = Annotated[UUID, Header(alias="X-Buyer-Id")]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
ListLimit = Annotated[int, Query(ge=1, le=100)]


class DisruptionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disruption_type: DisruptionType
    effective_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=2000)


class DepartureWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime


class ReplacementProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_operator_id: UUID | None = None
    proposed_aircraft_id: UUID | None = None
    departure_window: DepartureWindowRequest | None = None
    source: str = Field(min_length=1, max_length=64)
    source_evidence: str | None = Field(default=None, max_length=2000)


class CommercialChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    known_adjustment_minor: int = 0
    conditional_adjustment_minor: int = 0
    terms_summary: str | None = Field(default=None, max_length=2000)


class BuyerDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    commercial_change_id: UUID | None = None
    decision: DisruptionBuyerDecisionValue
    note: str | None = Field(default=None, max_length=1000)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    outcome: str = Field(min_length=1, max_length=2000)


class DisruptionResponse(BaseModel):
    id: UUID
    version: int
    booking_id: UUID
    disruption_type: DisruptionType
    status: DisruptionStatus
    detected_at: datetime
    effective_at: datetime | None
    reason: str
    current_proposal_id: UUID | None
    current_commercial_change_id: UUID | None
    latest_buyer_decision_id: UUID | None
    selected_proposal_id: UUID | None
    selected_commercial_change_id: UUID | None
    selected_buyer_decision_id: UUID | None
    resolved_at: datetime | None
    resolution_outcome: str | None
    booking_state_at_resolution: BookingState | None


class DisruptionListResponse(BaseModel):
    booking_id: UUID
    returned_count: int
    disruptions: list[DisruptionResponse]


class ReplacementProposalResponse(BaseModel):
    id: UUID
    disruption_id: UUID
    revision_number: int
    supersedes_proposal_id: UUID | None
    status: str
    proposed_operator_id: UUID
    proposed_aircraft_id: UUID
    departure_window: DepartureWindowRequest | None
    requires_buyer_decision: bool
    source: str
    source_evidence: str | None
    proposed_at: datetime
    superseded_at: datetime | None


class ReplacementProposalListResponse(BaseModel):
    disruption_id: UUID
    returned_count: int
    proposals: list[ReplacementProposalResponse]


class CommercialChangeResponse(BaseModel):
    id: UUID
    disruption_id: UUID
    proposal_id: UUID
    revision_number: int
    supersedes_change_id: UUID | None
    status: str
    original_quote_id: UUID
    currency: str
    normalization_version: str
    original_expected_total_minor: int
    original_worst_case_total_minor: int
    known_adjustment_minor: int
    conditional_adjustment_minor: int
    resulting_expected_total_minor: int
    resulting_worst_case_total_minor: int
    terms_summary: str | None
    created_at: datetime
    superseded_at: datetime | None


class BuyerDecisionResponse(BaseModel):
    id: UUID
    disruption_id: UUID
    proposal_id: UUID
    commercial_change_id: UUID | None
    buyer_id: UUID
    decision: DisruptionBuyerDecisionValue
    decided_at: datetime
    note: str | None


def _service(session: Session) -> DisruptionService:
    return DisruptionService(
        disruptions=SqlAlchemyDisruptionRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        organizations=SqlAlchemyOrganizationRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        fleet_timeline=SqlAlchemyFleetTimelineRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _party(buyer_id: UUID | None, operator_id: UUID | None) -> DisruptionPartyContext:
    return DisruptionPartyContext(
        buyer_id=OrganizationId(buyer_id) if buyer_id is not None else None,
        operator_id=OperatorId(operator_id) if operator_id is not None else None,
    )


def _actor_scope(buyer_id: UUID | None, operator_id: UUID | None) -> str:
    party = _party(buyer_id, operator_id)
    if party.buyer_id is not None:
        return f"buyer:{party.buyer_id}"
    assert party.operator_id is not None
    return f"operator:{party.operator_id}"


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


def _disruption_response(disruption: Disruption) -> DisruptionResponse:
    return DisruptionResponse(
        id=disruption.id.value,
        version=disruption.version,
        booking_id=disruption.booking_id.value,
        disruption_type=disruption.disruption_type,
        status=disruption.status,
        detected_at=disruption.detected_at,
        effective_at=disruption.effective_at,
        reason=disruption.reason,
        current_proposal_id=(
            disruption.current_proposal_id.value
            if disruption.current_proposal_id is not None
            else None
        ),
        current_commercial_change_id=(
            disruption.current_commercial_change_id.value
            if disruption.current_commercial_change_id is not None
            else None
        ),
        latest_buyer_decision_id=(
            disruption.latest_buyer_decision_id.value
            if disruption.latest_buyer_decision_id is not None
            else None
        ),
        selected_proposal_id=(
            disruption.selected_proposal_id.value
            if disruption.selected_proposal_id is not None
            else None
        ),
        selected_commercial_change_id=(
            disruption.selected_commercial_change_id.value
            if disruption.selected_commercial_change_id is not None
            else None
        ),
        selected_buyer_decision_id=(
            disruption.selected_buyer_decision_id.value
            if disruption.selected_buyer_decision_id is not None
            else None
        ),
        resolved_at=disruption.resolved_at,
        resolution_outcome=disruption.resolution_outcome,
        booking_state_at_resolution=disruption.booking_state_at_resolution,
    )


def _proposal_response(proposal: ReplacementProposal) -> ReplacementProposalResponse:
    window = None
    if proposal.departure_window is not None:
        window = DepartureWindowRequest(
            start=proposal.departure_window.start,
            end=proposal.departure_window.end,
        )
    return ReplacementProposalResponse(
        id=proposal.id.value,
        disruption_id=proposal.disruption_id.value,
        revision_number=proposal.revision_number,
        supersedes_proposal_id=(
            proposal.supersedes_proposal_id.value
            if proposal.supersedes_proposal_id is not None
            else None
        ),
        status=proposal.status.value,
        proposed_operator_id=proposal.proposed_operator_id.value,
        proposed_aircraft_id=proposal.proposed_aircraft_id.value,
        departure_window=window,
        requires_buyer_decision=proposal.requires_buyer_decision,
        source=proposal.source,
        source_evidence=proposal.source_evidence,
        proposed_at=proposal.proposed_at,
        superseded_at=proposal.superseded_at,
    )


def _commercial_response(change: DisruptionCommercialChange) -> CommercialChangeResponse:
    return CommercialChangeResponse(
        id=change.id.value,
        disruption_id=change.disruption_id.value,
        proposal_id=change.proposal_id.value,
        revision_number=change.revision_number,
        supersedes_change_id=(
            change.supersedes_change_id.value if change.supersedes_change_id is not None else None
        ),
        status=change.status.value,
        original_quote_id=change.original_quote_id.value,
        currency=str(change.currency),
        normalization_version=change.normalization_version,
        original_expected_total_minor=change.original_expected_total.amount_minor,
        original_worst_case_total_minor=change.original_worst_case_total.amount_minor,
        known_adjustment_minor=change.known_adjustment.amount_minor,
        conditional_adjustment_minor=change.conditional_adjustment.amount_minor,
        resulting_expected_total_minor=change.resulting_expected_total.amount_minor,
        resulting_worst_case_total_minor=change.resulting_worst_case_total.amount_minor,
        terms_summary=change.terms_summary,
        created_at=change.created_at,
        superseded_at=change.superseded_at,
    )


def _decision_response(decision: DisruptionBuyerDecision) -> BuyerDecisionResponse:
    return BuyerDecisionResponse(
        id=decision.id.value,
        disruption_id=decision.disruption_id.value,
        proposal_id=decision.proposal_id.value,
        commercial_change_id=(
            decision.commercial_change_id.value
            if decision.commercial_change_id is not None
            else None
        ),
        buyer_id=decision.buyer_id.value,
        decision=decision.decision,
        decided_at=decision.decided_at,
        note=decision.note,
    )


@router.post(
    "/bookings/{booking_id}/disruptions",
    response_model=DisruptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_disruption(
    booking_id: UUID,
    body: DisruptionCreateRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
) -> DisruptionResponse:
    actor_scope = _actor_scope(buyer_id, operator_id)
    scope = f"POST:/v1/bookings/{booking_id}/disruptions:{actor_scope}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return DisruptionResponse.model_validate(stored.response_body)

        disruption = _service(session).create(
            booking_id=BookingId(booking_id),
            disruption_type=body.disruption_type,
            detected_at=datetime.now(UTC),
            effective_at=body.effective_at,
            reason=body.reason,
            party=_party(buyer_id, operator_id),
            correlation_id=correlation_id,
        )
        response = _disruption_response(disruption)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get(
    "/bookings/{booking_id}/disruptions",
    response_model=DisruptionListResponse,
)
def list_disruptions(
    booking_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: ListLimit = 100,
) -> DisruptionListResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        items = _service(session).list_for_booking(
            booking_id=BookingId(booking_id),
            party=_party(buyer_id, operator_id),
            limit=limit,
        )
    return DisruptionListResponse(
        booking_id=booking_id,
        returned_count=len(items),
        disruptions=[_disruption_response(item) for item in items],
    )


@router.get("/disruptions/{disruption_id}", response_model=DisruptionResponse)
def get_disruption(
    disruption_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
) -> DisruptionResponse:
    return _disruption_response(
        _service(session).get(
            disruption_id=DisruptionId(disruption_id),
            party=_party(buyer_id, operator_id),
        )
    )


@router.post(
    "/disruptions/{disruption_id}/replacement-options",
    response_model=ReplacementProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_replacement(
    disruption_id: UUID,
    body: ReplacementProposalRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> ReplacementProposalResponse:
    scope = f"POST:/v1/disruptions/{disruption_id}/replacement-options:operator:{operator_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return ReplacementProposalResponse.model_validate(stored.response_body)

        window = None
        if body.departure_window is not None:
            window = TimeRange(body.departure_window.start, body.departure_window.end)
        proposal = _service(session).propose_replacement(
            disruption_id=DisruptionId(disruption_id),
            operator_id=OperatorId(operator_id),
            proposed_operator_id=(
                OperatorId(body.proposed_operator_id)
                if body.proposed_operator_id is not None
                else None
            ),
            proposed_aircraft_id=(
                AircraftId(body.proposed_aircraft_id)
                if body.proposed_aircraft_id is not None
                else None
            ),
            departure_window=window,
            source=body.source,
            source_evidence=body.source_evidence,
            proposed_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        response = _proposal_response(proposal)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get(
    "/disruptions/{disruption_id}/replacement-options",
    response_model=ReplacementProposalListResponse,
)
def list_replacement_options(
    disruption_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: ListLimit = 100,
) -> ReplacementProposalListResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        items = _service(session).list_proposals(
            disruption_id=DisruptionId(disruption_id),
            party=_party(buyer_id, operator_id),
            limit=limit,
        )
    return ReplacementProposalListResponse(
        disruption_id=disruption_id,
        returned_count=len(items),
        proposals=[_proposal_response(item) for item in items],
    )


@router.post(
    "/disruptions/{disruption_id}/requotes",
    response_model=CommercialChangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_requote(
    disruption_id: UUID,
    body: CommercialChangeRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> CommercialChangeResponse:
    scope = f"POST:/v1/disruptions/{disruption_id}/requotes:operator:{operator_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return CommercialChangeResponse.model_validate(stored.response_body)

        change = _service(session).create_commercial_change(
            disruption_id=DisruptionId(disruption_id),
            proposal_id=DisruptionProposalId(body.proposal_id),
            operator_id=OperatorId(operator_id),
            currency=Currency(body.currency.strip().upper()),
            known_adjustment_minor=body.known_adjustment_minor,
            conditional_adjustment_minor=body.conditional_adjustment_minor,
            terms_summary=body.terms_summary,
            created_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        response = _commercial_response(change)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/disruptions/{disruption_id}/buyer-decisions",
    response_model=BuyerDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def buyer_decision(
    disruption_id: UUID,
    body: BuyerDecisionRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> BuyerDecisionResponse:
    scope = f"POST:/v1/disruptions/{disruption_id}/buyer-decisions:buyer:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return BuyerDecisionResponse.model_validate(stored.response_body)

        decision = _service(session).buyer_decide(
            disruption_id=DisruptionId(disruption_id),
            proposal_id=DisruptionProposalId(body.proposal_id),
            commercial_change_id=(
                DisruptionCommercialChangeId(body.commercial_change_id)
                if body.commercial_change_id is not None
                else None
            ),
            buyer_id=OrganizationId(buyer_id),
            decision=body.decision,
            decided_at=datetime.now(UTC),
            note=body.note,
            correlation_id=correlation_id,
        )
        response = _decision_response(decision)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/disruptions/{disruption_id}/resolve",
    response_model=DisruptionResponse,
)
def resolve_disruption(
    disruption_id: UUID,
    body: ResolveRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> DisruptionResponse:
    scope = f"POST:/v1/disruptions/{disruption_id}/resolve:operator:{operator_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return DisruptionResponse.model_validate(stored.response_body)

        disruption = _service(session).resolve(
            disruption_id=DisruptionId(disruption_id),
            proposal_id=DisruptionProposalId(body.proposal_id),
            operator_id=OperatorId(operator_id),
            resolved_at=datetime.now(UTC),
            outcome=body.outcome,
            correlation_id=correlation_id,
        )
        response = _disruption_response(disruption)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
    return response
