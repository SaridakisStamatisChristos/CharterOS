from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.quote_comparison import (
    MissionQuoteComparison,
    QuoteComparisonService,
)
from charteros.application.quote_normalization import QuoteNormalizationService
from charteros.application.quotes import QuoteService
from charteros.application.tender_visibility import TenderVisibilityPolicy
from charteros.domain.aircraft import AircraftId
from charteros.domain.missions import MissionId
from charteros.domain.quotes import (
    PriceComponent,
    PriceComponentApplicability,
    PriceComponentCategory,
    Quote,
    QuoteId,
    QuoteStatus,
)
from charteros.domain.quotes.comparison import (
    EXPECTED_TOTAL_WEIGHT,
    OPERATIONAL_RISK_WEIGHT,
    PRICING_CONFIDENCE_WEIGHT,
    REPOSITION_WEIGHT,
    WORST_CASE_TOTAL_WEIGHT,
    ComparisonEligibilityReason,
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
    SqlAlchemyTenderRepository,
)
from charteros.infrastructure.db.repositories.catalog import (
    SqlAlchemyAirportRepository,
    SqlAlchemyIdempotencyRepository,
)
from charteros.infrastructure.db.repositories.matching import SqlAlchemyMatchingSnapshotRepository
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["quotes"])

ClockDep = Annotated[Clock, Depends(get_clock)]
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
    accepted_at: datetime | None
    rejected_at: datetime | None
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


class ComparisonWeightsResponse(BaseModel):
    expected_total_points: int
    worst_case_total_points: int
    reposition_points: int
    operational_risk_points: int
    pricing_confidence_points: int


class ComparisonScoreResponse(BaseModel):
    method: str
    total_basis_points: int | None
    expected_total_points: int | None
    worst_case_total_points: int | None
    reposition_points: int | None
    operational_risk_points: int | None
    pricing_confidence_points: int
    currency_scope: str
    cohort_size: int


class AircraftSuitabilityResponse(BaseModel):
    feasible: bool
    reason_codes: list[str]
    rejection_reasons: list[str]
    seat_capacity: int
    aircraft_range_nm: int
    required_range_nm: int
    reposition_distance_nm: Decimal | None
    timing_buffer_minutes: int | None
    schedule_risk_basis_points: int | None
    position_event_time: datetime | None
    position_recorded_at: datetime | None
    availability_recorded_at: datetime | None
    reference_profile_recorded_at: datetime | None


class QuoteComparisonEntryResponse(BaseModel):
    quote_id: UUID
    rfq_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    quote_revision_number: int
    quote_status: QuoteStatus
    valid_until: datetime
    commercial_valid: bool
    decision_eligible: bool
    eligibility_reasons: list[ComparisonEligibilityReason]
    normalization: QuoteNormalizationResponse
    submitted_repositioning_cost: MoneyResponse | None
    inclusions: list[str]
    exclusions: list[str]
    cancellation_terms: str | None
    payment_terms: str | None
    aircraft_suitability: AircraftSuitabilityResponse
    currency_rank: int | None
    score: ComparisonScoreResponse


class MissionQuoteComparisonResponse(BaseModel):
    comparison_policy_version: str
    matching_policy_version: str
    mission_id: UUID
    evaluated_at: datetime
    pricing_currencies: list[str]
    global_rank_available: bool
    ranking_scope: str
    free_text_terms_scored: bool
    score_weights: ComparisonWeightsResponse
    returned_count: int
    quotes: list[QuoteComparisonEntryResponse]


def _service(session: Session) -> QuoteService:
    return QuoteService(
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        aircraft=SqlAlchemyAircraftRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
    )


def _normalization_service(session: Session) -> QuoteNormalizationService:
    return QuoteNormalizationService(quotes=SqlAlchemyQuoteRepository(session))


def _comparison_service(session: Session) -> QuoteComparisonService:
    return QuoteComparisonService(
        missions=SqlAlchemyMissionRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        snapshots=SqlAlchemyMatchingSnapshotRepository(session),
    )


def _visibility_policy(session: Session) -> TenderVisibilityPolicy:
    return TenderVisibilityPolicy(
        tenders=SqlAlchemyTenderRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
    )


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
        accepted_at=quote.accepted_at,
        rejected_at=quote.rejected_at,
        expired_at=quote.expired_at,
        withdrawn_at=quote.withdrawn_at,
        superseded_at=quote.superseded_at,
    )


def _nm(tenths: int | None) -> Decimal | None:
    if tenths is None:
        return None
    return (Decimal(tenths) / Decimal(10)).quantize(Decimal("0.1"))


def _comparison_response(value: MissionQuoteComparison) -> MissionQuoteComparisonResponse:
    quotes: list[QuoteComparisonEntryResponse] = []
    for item in value.entries:
        suitability = item.aircraft_suitability
        quotes.append(
            QuoteComparisonEntryResponse(
                quote_id=item.quote.id.value,
                rfq_id=item.quote.rfq_id.value,
                operator_id=item.operator_id.value,
                aircraft_id=item.quote.aircraft_id.value,
                quote_revision_number=item.quote.revision_number,
                quote_status=item.quote.status,
                valid_until=item.quote.valid_until,
                commercial_valid=item.commercial_valid,
                decision_eligible=item.decision_eligible,
                eligibility_reasons=list(item.eligibility_reasons),
                normalization=_normalization_response(item.normalization),
                submitted_repositioning_cost=(
                    _money(item.quote.repositioning_cost)
                    if item.quote.repositioning_cost is not None
                    else None
                ),
                inclusions=list(item.quote.inclusions),
                exclusions=list(item.quote.exclusions),
                cancellation_terms=item.quote.cancellation_terms,
                payment_terms=item.quote.payment_terms,
                aircraft_suitability=AircraftSuitabilityResponse(
                    feasible=suitability.feasible,
                    reason_codes=[reason.value for reason in suitability.reason_codes],
                    rejection_reasons=[reason.value for reason in suitability.rejection_reasons],
                    seat_capacity=suitability.seat_capacity,
                    aircraft_range_nm=suitability.aircraft_range_nm,
                    required_range_nm=suitability.required_range_nm,
                    reposition_distance_nm=_nm(suitability.reposition_distance_tenths_nm),
                    timing_buffer_minutes=suitability.timing_buffer_minutes,
                    schedule_risk_basis_points=suitability.schedule_risk_basis_points,
                    position_event_time=suitability.position_event_time,
                    position_recorded_at=suitability.position_recorded_at,
                    availability_recorded_at=suitability.availability_recorded_at,
                    reference_profile_recorded_at=suitability.reference_profile_recorded_at,
                ),
                currency_rank=item.currency_rank,
                score=ComparisonScoreResponse(
                    method=item.score.method,
                    total_basis_points=item.score.total_basis_points,
                    expected_total_points=item.score.expected_total_points,
                    worst_case_total_points=item.score.worst_case_total_points,
                    reposition_points=item.score.reposition_points,
                    operational_risk_points=item.score.operational_risk_points,
                    pricing_confidence_points=item.score.pricing_confidence_points,
                    currency_scope=str(item.score.currency_scope),
                    cohort_size=item.score.cohort_size,
                ),
            )
        )

    return MissionQuoteComparisonResponse(
        comparison_policy_version=value.comparison_policy_version,
        matching_policy_version=value.matching_policy_version,
        mission_id=value.mission_id.value,
        evaluated_at=value.evaluated_at,
        pricing_currencies=[str(currency) for currency in value.pricing_currencies],
        global_rank_available=value.global_rank_available,
        ranking_scope="currency",
        free_text_terms_scored=False,
        score_weights=ComparisonWeightsResponse(
            expected_total_points=EXPECTED_TOTAL_WEIGHT,
            worst_case_total_points=WORST_CASE_TOTAL_WEIGHT,
            reposition_points=REPOSITION_WEIGHT,
            operational_risk_points=OPERATIONAL_RISK_WEIGHT,
            pricing_confidence_points=PRICING_CONFIDENCE_WEIGHT,
        ),
        returned_count=len(quotes),
        quotes=quotes,
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
    clock: ClockDep,
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
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.get("/rfqs/{rfq_id}/quotes", response_model=QuoteListResponse)
def list_quotes(rfq_id: UUID, session: SessionDep) -> QuoteListResponse:
    typed_rfq_id = RfqId(rfq_id)
    _visibility_policy(session).assert_rfq_quotes_visible(typed_rfq_id)
    items = _service(session).list_for_rfq(typed_rfq_id)
    return QuoteListResponse(rfq_id=rfq_id, quotes=[_response(item) for item in items])


@router.get(
    "/missions/{mission_id}/quotes/compare",
    response_model=MissionQuoteComparisonResponse,
)
def compare_mission_quotes(
    mission_id: UUID,
    session: SessionDep,
    clock: ClockDep,
) -> MissionQuoteComparisonResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        typed_mission_id = MissionId(mission_id)
        _visibility_policy(session).assert_mission_comparison_visible(typed_mission_id)
        comparison = _comparison_service(session).compare(
            mission_id=typed_mission_id,
            evaluated_at=clock.now(),
        )
    return _comparison_response(comparison)


@router.get("/quotes/{quote_id}", response_model=QuoteResponse)
def get_quote(quote_id: UUID, session: SessionDep) -> QuoteResponse:
    quote = _visibility_policy(session).get_visible_quote(QuoteId(quote_id))
    return _response(quote)


@router.get(
    "/quotes/{quote_id}/normalization",
    response_model=QuoteNormalizationResponse,
)
def get_quote_normalization(
    quote_id: UUID,
    session: SessionDep,
) -> QuoteNormalizationResponse:
    typed_quote_id = QuoteId(quote_id)
    _visibility_policy(session).get_visible_quote(typed_quote_id)
    normalized = _normalization_service(session).normalize(typed_quote_id)
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
    clock: ClockDep,
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
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post("/quotes/{quote_id}/withdraw", response_model=QuoteResponse)
def withdraw_quote(
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
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
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )


@router.post("/quotes/{quote_id}/expire", response_model=QuoteResponse)
def expire_quote(
    quote_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    clock: ClockDep,
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
                now=clock.now(),
                correlation_id=correlation_id,
            ),
        )
