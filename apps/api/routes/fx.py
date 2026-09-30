from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.fx import FxService
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.domain.fx import FxRateId, FxRateObservation
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyDomainEventRepository,
    SqlAlchemyFxLockRepository,
    SqlAlchemyFxRateRepository,
    SqlAlchemyIdempotencyRepository,
)
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1/fx", tags=["fx"])

ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class FxRateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_currency: str = Field(min_length=3, max_length=3)
    target_currency: str = Field(min_length=3, max_length=3)
    rate: StrictStr = Field(min_length=1, max_length=80)
    source_minor_exponent: int = Field(ge=0, le=9)
    target_minor_exponent: int = Field(ge=0, le=9)
    fx_source: str = Field(min_length=1, max_length=64)
    fx_source_version: str = Field(min_length=1, max_length=128)
    fx_timestamp: datetime


class FxRateCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: str = Field(min_length=1, max_length=80)
    fx_source_version: str = Field(min_length=1, max_length=128)


class FxRateResponse(BaseModel):
    id: UUID
    version: int
    source_currency: str
    target_currency: str
    rate: str
    source_minor_exponent: int
    target_minor_exponent: int
    fx_source: str
    fx_source_version: str
    fx_timestamp: datetime
    recorded_at: datetime
    revision_number: int
    supersedes_rate_id: UUID | None


def _service(session: Session) -> FxService:
    return FxService(
        rates=SqlAlchemyFxRateRepository(session),
        locks=SqlAlchemyFxLockRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
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


def _response(rate: FxRateObservation) -> FxRateResponse:
    return FxRateResponse(
        id=rate.id.value,
        version=rate.version,
        source_currency=str(rate.source_currency),
        target_currency=str(rate.target_currency),
        rate=rate.rate_text,
        source_minor_exponent=rate.source_minor_exponent,
        target_minor_exponent=rate.target_minor_exponent,
        fx_source=rate.fx_source,
        fx_source_version=rate.fx_source_version,
        fx_timestamp=rate.fx_timestamp,
        recorded_at=rate.recorded_at,
        revision_number=rate.revision_number,
        supersedes_rate_id=(
            rate.supersedes_rate_id.value if rate.supersedes_rate_id is not None else None
        ),
    )


@router.post("/rates", response_model=FxRateResponse, status_code=status.HTTP_201_CREATED)
def create_rate(
    body: FxRateCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> FxRateResponse:
    scope = "POST:/v1/fx/rates"
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
            return FxRateResponse.model_validate(stored.response_body)

        rate = _service(session).record_rate(
            source_currency=Currency(body.source_currency.strip().upper()),
            target_currency=Currency(body.target_currency.strip().upper()),
            rate_text=body.rate,
            source_minor_exponent=body.source_minor_exponent,
            target_minor_exponent=body.target_minor_exponent,
            fx_source=body.fx_source,
            fx_source_version=body.fx_source_version,
            fx_timestamp=body.fx_timestamp,
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _response(rate)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/rates/{rate_id}/corrections",
    response_model=FxRateResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_rate(
    rate_id: UUID,
    body: FxRateCorrection,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
) -> FxRateResponse:
    scope = f"POST:/v1/fx/rates/{rate_id}/corrections"
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
            return FxRateResponse.model_validate(stored.response_body)

        rate = _service(session).correct_rate(
            rate_id=FxRateId(rate_id),
            rate_text=body.rate,
            fx_source_version=body.fx_source_version,
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _response(rate)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/rates/{rate_id}", response_model=FxRateResponse)
def get_rate(rate_id: UUID, session: SessionDep) -> FxRateResponse:
    rate = SqlAlchemyFxRateRepository(session).get(FxRateId(rate_id))
    if rate is None:
        raise EntityNotFoundError("FX rate observation does not exist")
    return _response(rate)
