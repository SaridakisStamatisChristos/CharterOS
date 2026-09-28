from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_correlation_id, get_session
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.quote_normalization import QuoteNormalizationService
from charteros.application.quotes import QuoteService
from charteros.domain.aircraft import AircraftId
from charteros.domain.quotes import (
    PriceComponent,
    PriceComponentApplicability,
    PriceComponentCategory,
    Quote,
    QuoteId,
    QuoteStatus,
)
from charteros.domain.quotes.normalization import (
    NormalizationCaveatCode,
    NormalizedFeeCategory,
    PricingConfidence,
    QuoteNormalization,
    UnresolvedComponentReason,
)
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository

router = APIRouter(prefix="/v1", tags=["quotes"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class PriceComponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: PriceComponentCategory
    label: str = Field(min_length=1, max_length=200)
    amount_minor: int = Field(ge=0)
    applicability: PriceComponentApplicability = PriceComponentApplicability.KNOWN
    condition: str | None = Field(default=None, max_length=500)


class QuoteTermsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aircraft_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    base_amount_minor: int = Field(gt=0)
    repositioning_amount_minor: int | None = Field(default=None, ge=0)
    price_components: list[PriceComponentRequest] = Field(default_factory=list, max_length=128)
    inclusions: list[str] = Field(default_factory=list, max_length=128)
    exclusions: list[str] = Field(default_factory=list, max_length=128)
    cancellation_terms: str | None = Field(default=None, max_length=4000)
    payment_terms: str | None = Field(default=None, max_length=4000)
    valid_until: datetime


class MoneyResponse(BaseModel):
    amount_minor: int
    currency: str


class PriceComponentResponse(BaseModel):
    category: PriceComponentCategory
    label: str
    amount: MoneyResponse
    applicability: PriceComponentApplicability = PriceComponentApplicability.KNOWN
    condition: str | None


class QuoteResponse(BaseModel):
    id: UUID
    version: int
    rfq_id: UUID
    aircraft_id: UUID
    status: QuoteStatus
    currency: str
    base_price: MoneyResponse
    repositioning_cost: MoneyResponse | None
    price_components: list[PriceComponentResponse]
    submitted_total: MoneyResponse
    inclusions: list[str]
    exclusions: list[str]
    cancellation_terms: str | None
    payment_terms: str | None
    valid_until: datetime
    revision_number: int
    supersedes_quote_id: UUID | None
    submitted_at: datetime
    is_current: bool
    expired_at: datetime | None
    withdrawn_at: datetime | None
    superseded_at: datetime | None


class QuoteListResponse(BaseModel):
    rfq_id: UUID
    quotes: list[QuoteResponse]


class NormalizedFeeResponse(BaseModel):
    category: NormalizedFeeCategory
    label: str
    amount: MoneyResponse
    condition: str | None


class UnresolvedPricingComponentResponse(BaseModel):
    label: str
    reason: UnresolvedComponentReason


class NormalizationCaveatResponse(BaseModel):
    code: NormalizationCaveatCode
    message: str


class QuoteNormalizationResponse(BaseModel):
    normalization_version: str
    quote_id: UUID
    quote_revision_number: int
    currency: str
    base_price: MoneyResponse
    known_fees: list[NormalizedFeeResponse]
    conditional_fees: list[NormalizedFeeResponse]
    excluded_fees: list[str]
    expected_total: MoneyResponse
    worst_case_total: MoneyResponse
    totals_complete: bool
    confidence: PricingConfidence
    caveats: list[NormalizationCaveatResponse]
    unresolved_components: list[UnresolvedPricingComponentResponse]


def _service(session: Session) -> QuoteService:
    return QuoteService(
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _normalization_service(session: Session) -> QuoteNormalizationService:
    return QuoteNormalizationService(quotes=SqlAlchemyQuoteRepository(session))


def _money(value: Money) -> MoneyResponse:
    return MoneyResponse(amount_minor=value.amount_minor, currency=str(value.currency))


def _normalization_response(value: QuoteNormalization) -> QuoteNormalizationResponse:
    return QuoteNormalizationResponse(
        normalization_version=value.normalization_version,
        quote_id=value.quote_id.value,
        quote_revision_number=value.quote_revision_number,
        currency=str(value.currency),
        base_price=_money(value.base_price),
        known_fees=[
            NormalizedFeeResponse(
                category=fee.category,
                label=fee.label,
                amount=_money(fee.amount),
                condition=fee.condition,
            )
            for fee in value.known_fees
        ],
        conditional_fees=[
            NormalizedFeeResponse(
                category=fee.category,
                label=fee.label,
                amount=_money(fee.amount),
                condition=fee.condition,
            )
            for fee in value.conditional_fees
        ],
        excluded_fees=list(value.excluded_fees),
        expected_total=_money(value.expected_total),
        worst_case_total=_money(value.worst_case_total),
        totals_complete=value.totals_complete,
        confidence=value.confidence,
        caveats=[
            NormalizationCaveatResponse(code=item.code, message=item.message)
            for item in value.caveats
        ],
        unresolved_components=[
            UnresolvedPricingComponentResponse(label=item.label, reason=item.reason)
            for item in value.unresolved_components
        ],
    )


def _response(quote: Quote) -> QuoteResponse:
    return QuoteResponse(
        id=quote.id.value,
        version=quote.version,
        rfq_id=quote.rfq_id.value,
        aircraft_id=quote.aircraft_id.value,
        status=quote.status,
        currency=str(quote.currency),
        base_price=_money(quote.base_price),
        repositioning_cost=(
            _money(quote.repositioning_cost) if quote.repositioning_cost is not None else None
        ),
        price_components=[
            PriceComponentResponse(
                category=component.category,
                label=component.label,
                amount=_money(component.amount),
                applicability=component.applicability,
                condition=component.condition,
            )
            for component in quote.price_components
        ],
        submitted_total=_money(quote.submitted_total),
        inclusions=list(quote.inclusions),
        exclusions=list(quote.exclusions),
        cancellation_terms=quote.cancellation_terms,
        payment_terms=quote.payment_terms,
        valid_until=quote.valid_until,
        revision_number=quote.revision_number,
        supersedes_quote_id=(
            quote.supersedes_quote_id.value if quote.supersedes_quote_id is not None else None
        ),
        submitted_at=quote.submitted_at,
        is_current=quote.is_current,
        expired_at=quote.expired_at,
        withdrawn_at=quote.withdrawn_at,
        superseded_at=quote.superseded_at,
    )


def _terms(
    body: QuoteTermsRequest,
) -> tuple[
    AircraftId,
    Money,
    tuple[PriceComponent, ...],
    Money | None,
]:
    currency = Currency(body.currency.strip().upper())
    base_price = Money(body.base_amount_minor, currency)
    repositioning = (
        Money(body.repositioning_amount_minor, currency)
        if body.repositioning_amount_minor is not None
        else None
    )
    components = tuple(
        PriceComponent(
            category=item.category,
            label=item.label,
            amount=Money(item.amount_minor, currency),
            applicability=item.applicability,
            condition=item.condition,
        )
        for item in body.price_components
    )
    return AircraftId(body.aircraft_id), base_price, components, repositioning


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
    action: Callable[[], Quote],
) -> QuoteResponse:
    idempotency = SqlAlchemyIdempotencyRepository(session)
    idempotency.lock(scope, key)
    stored = _stored_response(
        idempotency,
        scope=scope,
        key=key,
        request_hash=request_hash,
    )
    if stored is not None:
        return QuoteResponse.model_validate(stored.response_body)

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
    "/rfqs/{rfq_id}/quotes",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_quote(
    rfq_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> QuoteResponse:
    scope = f"POST:/v1/rfqs/{rfq_id}/quotes"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        aircraft_id, base_price, components, repositioning = _terms(body)
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            action=lambda: _service(session).submit(
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
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            ),
        )


@router.get("/rfqs/{rfq_id}/quotes", response_model=QuoteListResponse)
def list_quotes(rfq_id: UUID, session: SessionDep) -> QuoteListResponse:
    items = _service(session).list_for_rfq(RfqId(rfq_id))
    return QuoteListResponse(rfq_id=rfq_id, quotes=[_response(item) for item in items])


@router.get("/quotes/{quote_id}", response_model=QuoteResponse)
def get_quote(quote_id: UUID, session: SessionDep) -> QuoteResponse:
    return _response(_service(session).get(QuoteId(quote_id)))


@router.get(
    "/quotes/{quote_id}/normalization",
    response_model=QuoteNormalizationResponse,
)
def get_quote_normalization(
    quote_id: UUID,
    session: SessionDep,
) -> QuoteNormalizationResponse:
    normalized = _normalization_service(session).normalize(QuoteId(quote_id))
    return _normalization_response(normalized)


@router.post(
    "/quotes/{quote_id}/revise",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def revise_quote(
    quote_id: UUID,
    body: QuoteTermsRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> QuoteResponse:
    scope = f"POST:/v1/quotes/{quote_id}/revise"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        aircraft_id, base_price, components, repositioning = _terms(body)
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_201_CREATED,
            action=lambda: _service(session).revise(
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
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            ),
        )


@router.post("/quotes/{quote_id}/withdraw", response_model=QuoteResponse)
def withdraw_quote(
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> QuoteResponse:
    scope = f"POST:/v1/quotes/{quote_id}/withdraw"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            action=lambda: _service(session).withdraw(
                quote_id=QuoteId(quote_id),
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            ),
        )


@router.post("/quotes/{quote_id}/expire", response_model=QuoteResponse)
def expire_quote(
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> QuoteResponse:
    scope = f"POST:/v1/quotes/{quote_id}/expire"
    request_hash = canonical_request_hash({})
    with session.begin():
        return _run_idempotent(
            session=session,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            success_status=status.HTTP_200_OK,
            action=lambda: _service(session).expire(
                quote_id=QuoteId(quote_id),
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            ),
        )
