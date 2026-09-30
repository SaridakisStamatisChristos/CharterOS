from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.contracts import ContractService
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.bookings import BookingId
from charteros.domain.contracts import Contract, ContractId, ContractStatus
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyBookingRepository,
    SqlAlchemyContractRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyMissionRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["contracts"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class CreateContractRequest(BaseModel):
    document_reference: str = Field(min_length=1, max_length=1000)
    document_version: int = Field(ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class ContractResponse(BaseModel):
    id: UUID
    version: int
    booking_id: UUID
    buyer_id: UUID
    operator_id: UUID
    document_reference: str
    document_version: int
    metadata: dict[str, str]
    status: ContractStatus
    created_at: datetime
    buyer_signed_at: datetime | None
    operator_signed_at: datetime | None
    accepted_at: datetime | None


def _service(session: Session) -> ContractService:
    return ContractService(
        contracts=SqlAlchemyContractRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _response(contract: Contract) -> ContractResponse:
    return ContractResponse(
        id=contract.id.value,
        version=contract.version,
        booking_id=contract.booking_id.value,
        buyer_id=contract.buyer_id.value,
        operator_id=contract.operator_id.value,
        document_reference=contract.document_reference,
        document_version=contract.document_version,
        metadata=dict(contract.metadata),
        status=contract.status,
        created_at=contract.created_at,
        buyer_signed_at=contract.buyer_signed_at,
        operator_signed_at=contract.operator_signed_at,
        accepted_at=contract.accepted_at,
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
    action: Callable[[], Contract],
) -> ContractResponse:
    idempotency = SqlAlchemyIdempotencyRepository(session)
    idempotency.lock(scope, key)
    stored = _stored_response(
        idempotency,
        scope=scope,
        key=key,
        request_hash=request_hash,
    )
    if stored is not None:
        return ContractResponse.model_validate(stored.response_body)

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
    "/bookings/{booking_id}/contract",
    response_model=ContractResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contract(
    booking_id: UUID,
    request: CreateContractRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: ClockDep,
) -> ContractResponse:
    scope = f"POST:/v1/bookings/{booking_id}/contract"
    request_hash = canonical_request_hash(request.model_dump(mode="json"))
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            action=lambda: _service(session).create_contract(
                booking_id=BookingId(booking_id),
                document_reference=request.document_reference,
                document_version=request.document_version,
                metadata=request.metadata,
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post(
    "/contracts/{contract_id}/accept/buyer",
    response_model=ContractResponse,
)
def accept_contract_buyer(
    contract_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: ClockDep,
) -> ContractResponse:
    scope = f"POST:/v1/contracts/{contract_id}/accept/buyer"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            action=lambda: _service(session).accept_buyer(
                contract_id=ContractId(contract_id),
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post(
    "/contracts/{contract_id}/accept/operator",
    response_model=ContractResponse,
)
def accept_contract_operator(
    contract_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: ClockDep,
) -> ContractResponse:
    scope = f"POST:/v1/contracts/{contract_id}/accept/operator"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            action=lambda: _service(session).accept_operator(
                contract_id=ContractId(contract_id),
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.get("/contracts/{contract_id}", response_model=ContractResponse)
def get_contract(contract_id: UUID, session: SessionDep) -> ContractResponse:
    return _response(_service(session).get(ContractId(contract_id)))


@router.get("/bookings/{booking_id}/contract", response_model=ContractResponse)
def get_booking_contract(booking_id: UUID, session: SessionDep) -> ContractResponse:
    return _response(_service(session).get_for_booking(BookingId(booking_id)))
