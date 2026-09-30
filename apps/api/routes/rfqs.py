from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.shared.clock import Clock
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.rfqs import RfqService
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import Rfq, RfqId, RfqStatus
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyDomainEventRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyRfqRepository,
    SqlAlchemyTenderRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository

router = APIRouter(prefix="/v1", tags=["rfqs"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class RfqCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_id: UUID
    response_deadline: datetime


class RfqDecline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class RfqResponse(BaseModel):
    id: UUID
    version: int
    mission_id: UUID
    operator_id: UUID
    status: RfqStatus
    created_at: datetime
    sent_at: datetime | None
    response_deadline: datetime | None
    acknowledged_at: datetime | None
    declined_at: datetime | None
    expired_at: datetime | None
    decline_reason: str | None


class RfqListResponse(BaseModel):
    mission_id: UUID
    rfqs: list[RfqResponse]


def _service(session: Session) -> RfqService:
    return RfqService(
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
    )


def _response(rfq: Rfq) -> RfqResponse:
    return RfqResponse(
        id=rfq.id.value,
        version=rfq.version,
        mission_id=rfq.mission_id.value,
        operator_id=rfq.operator_id.value,
        status=rfq.status,
        created_at=rfq.created_at,
        sent_at=rfq.sent_at,
        response_deadline=rfq.response_deadline,
        acknowledged_at=rfq.acknowledged_at,
        declined_at=rfq.declined_at,
        expired_at=rfq.expired_at,
        decline_reason=rfq.decline_reason,
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
    action: Callable[[], Rfq],
) -> RfqResponse:
    idempotency = SqlAlchemyIdempotencyRepository(session)
    idempotency.lock(scope, key)
    stored = _stored_response(
        idempotency,
        scope=scope,
        key=key,
        request_hash=request_hash,
    )
    if stored is not None:
        return RfqResponse.model_validate(stored.response_body)

    response = _response(action())
    idempotency.add(
        scope=scope,
        key=key,
        request_hash=request_hash,
        status_code=status.HTTP_200_OK,
        response_body=response.model_dump(mode="json"),
    )
    return response


@router.post(
    "/missions/{mission_id}/rfqs",
    response_model=RfqResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rfq(
    mission_id: UUID,
    body: RfqCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> RfqResponse:
    scope = f"POST:/v1/missions/{mission_id}/rfqs"
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
            return RfqResponse.model_validate(stored.response_body)

        response = _response(
            _service(session).create_and_send(
                mission_id=MissionId(mission_id),
                operator_id=OperatorId(body.operator_id),
                response_deadline=body.response_deadline,
                now=clock.now(),
                correlation_id=correlation_id,
            )
        )
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/missions/{mission_id}/rfqs", response_model=RfqListResponse)
def list_rfqs(mission_id: UUID, session: SessionDep) -> RfqListResponse:
    items = _service(session).list_for_mission(MissionId(mission_id))
    return RfqListResponse(
        mission_id=mission_id,
        rfqs=[_response(item) for item in items],
    )


@router.post("/rfqs/{rfq_id}/acknowledge", response_model=RfqResponse)
def acknowledge_rfq(
    rfq_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> RfqResponse:
    scope = f"POST:/v1/rfqs/{rfq_id}/acknowledge"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            action=lambda: _service(session).acknowledge(
                rfq_id=RfqId(rfq_id),
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post("/rfqs/{rfq_id}/decline", response_model=RfqResponse)
def decline_rfq(
    rfq_id: UUID,
    body: RfqDecline,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> RfqResponse:
    scope = f"POST:/v1/rfqs/{rfq_id}/decline"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            action=lambda: _service(session).decline(
                rfq_id=RfqId(rfq_id),
                reason=body.reason,
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post("/rfqs/{rfq_id}/expire", response_model=RfqResponse)
def expire_rfq(
    rfq_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> RfqResponse:
    scope = f"POST:/v1/rfqs/{rfq_id}/expire"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            action=lambda: _service(session).expire(
                rfq_id=RfqId(rfq_id),
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )
