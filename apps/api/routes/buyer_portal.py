from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_correlation_id, get_session
from charteros.application.buyer_portal import BuyerPortalService
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.matching import MatchingService
from charteros.application.missions import MissionService
from charteros.application.procurement_approvals import ProcurementApprovalService
from charteros.application.quote_comparison import QuoteComparisonService
from charteros.application.rfqs import RfqService
from charteros.application.tender_visibility import TenderVisibilityPolicy
from charteros.domain.airports import AirportId
from charteros.domain.bookings import Booking, BookingState
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import (
    ProcurementApproval,
    ProcurementApprovalId,
    ProcurementApprovalStatus,
)
from charteros.domain.quotes import QuoteId
from charteros.domain.rfqs import Rfq, RfqStatus
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAirportRepository,
    SqlAlchemyBookingRepository,
    SqlAlchemyBuyerProcurementAuditRepository,
    SqlAlchemyContractRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyMatchingSnapshotRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyProcurementApprovalRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
    SqlAlchemyTenderRepository,
)

router = APIRouter(prefix="/v1/buyer-portal", tags=["buyer-portal"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
BuyerIdDep = Annotated[UUID, Header(alias="X-Buyer-Id")]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
KnownAsOf = Annotated[datetime | None, Query(description="UTC knowledge-time cutoff for replay")]
SupplierLimit = Annotated[int, Query(ge=1, le=100)]
AuditLimit = Annotated[int, Query(ge=1, le=200)]


class TimeWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime


class MoneyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class BuyerMissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_airport_id: UUID
    destination_airport_id: UUID
    departure_window: TimeWindowRequest
    passenger_count: int = Field(gt=0)
    max_budget: MoneyRequest | None = None
    special_requirements: list[str] = Field(default_factory=list, max_length=64)


class TimeWindowResponse(BaseModel):
    start: datetime
    end: datetime


class MoneyResponse(BaseModel):
    amount_minor: int
    currency: str


class BuyerMissionResponse(BaseModel):
    id: UUID
    version: int
    buyer_id: UUID
    origin_airport_id: UUID
    destination_airport_id: UUID
    departure_window: TimeWindowResponse
    passenger_count: int
    max_budget: MoneyResponse | None
    special_requirements: list[str]
    status: MissionStatus


class SupplierCandidateResponse(BaseModel):
    supplier_rank: int
    operator_id: UUID
    best_aircraft_id: UUID
    aircraft_type_id: UUID
    matching_rank: int
    reason_codes: list[str]
    estimated_operating_cost: MoneyResponse
    budget_comparison: str
    budget_delta_minor: int | None
    reposition_distance_nm: Decimal
    timing_buffer_minutes: int
    schedule_risk_basis_points: int
    score_total_basis_points: int
    score_method: str


class SupplierSearchResponse(BaseModel):
    mission_id: UUID
    matching_policy_version: str
    reference_currency: str
    known_as_of: datetime
    candidate_aircraft_count: int
    feasible_aircraft_count: int
    supplier_count: int
    returned_count: int
    suppliers: list[SupplierCandidateResponse]


class BuyerRfqIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_ids: list[UUID] = Field(min_length=1, max_length=100)
    response_deadline: datetime


class BuyerRfqResponse(BaseModel):
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


class BuyerRfqBatchResponse(BaseModel):
    mission_id: UUID
    issued_count: int
    rfqs: list[BuyerRfqResponse]


class QuoteComparisonEntryResponse(BaseModel):
    quote_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    revision_number: int
    quote_status: str
    valid_until: datetime
    currency: str
    expected_total: MoneyResponse
    worst_case_total: MoneyResponse
    totals_complete: bool
    pricing_confidence: str
    commercial_valid: bool
    aircraft_feasible: bool
    decision_eligible: bool
    eligibility_reasons: list[str]
    rejection_reasons: list[str]
    currency_rank: int | None
    score_method: str
    score_total_basis_points: int | None


class BuyerQuoteComparisonResponse(BaseModel):
    mission_id: UUID
    comparison_policy_version: str
    matching_policy_version: str
    evaluated_at: datetime
    pricing_currencies: list[str]
    global_rank_available: bool
    quotes: list[QuoteComparisonEntryResponse]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class ApprovalResponse(BaseModel):
    id: UUID
    version: int
    mission_id: UUID
    buyer_id: UUID
    quote_id: UUID
    status: ProcurementApprovalStatus
    approved_at: datetime
    note: str | None
    supersedes_approval_id: UUID | None
    superseded_at: datetime | None
    consumed_at: datetime | None
    booking_id: UUID | None


class BookingStatusResponse(BaseModel):
    id: UUID
    version: int
    mission_id: UUID
    accepted_quote_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    state: BookingState
    created_at: datetime
    state_changed_at: datetime


class AwardResponse(BaseModel):
    approval: ApprovalResponse
    booking: BookingStatusResponse


class AuditEventResponse(BaseModel):
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None


class AuditTrailResponse(BaseModel):
    mission_id: UUID
    returned_count: int
    truncated: bool
    events: list[AuditEventResponse]


def _portal(session: Session) -> BuyerPortalService:
    return BuyerPortalService(
        organizations=SqlAlchemyOrganizationRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        audit=SqlAlchemyBuyerProcurementAuditRepository(session),
    )


def _mission_service(session: Session) -> MissionService:
    return MissionService(
        missions=SqlAlchemyMissionRepository(session),
        organizations=SqlAlchemyOrganizationRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _rfq_service(session: Session) -> RfqService:
    return RfqService(
        rfqs=SqlAlchemyRfqRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
    )


def _comparison_service(session: Session) -> QuoteComparisonService:
    return QuoteComparisonService(
        missions=SqlAlchemyMissionRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        snapshots=SqlAlchemyMatchingSnapshotRepository(session),
    )


def _matching_service(session: Session) -> MatchingService:
    return MatchingService(
        missions=SqlAlchemyMissionRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        snapshots=SqlAlchemyMatchingSnapshotRepository(session),
    )


def _visibility_policy(session: Session) -> TenderVisibilityPolicy:
    return TenderVisibilityPolicy(
        tenders=SqlAlchemyTenderRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
    )


def _approval_service(session: Session) -> ProcurementApprovalService:
    return ProcurementApprovalService(
        approvals=SqlAlchemyProcurementApprovalRepository(session),
        organizations=SqlAlchemyOrganizationRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        contracts=SqlAlchemyContractRepository(session),
        tenders=SqlAlchemyTenderRepository(session),
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


def _mission_response(mission: Mission) -> BuyerMissionResponse:
    budget = None
    if mission.max_budget is not None:
        budget = MoneyResponse(
            amount_minor=mission.max_budget.amount_minor,
            currency=str(mission.max_budget.currency),
        )
    return BuyerMissionResponse(
        id=mission.id.value,
        version=mission.version,
        buyer_id=mission.buyer_id.value,
        origin_airport_id=mission.origin_airport_id.value,
        destination_airport_id=mission.destination_airport_id.value,
        departure_window=TimeWindowResponse(
            start=mission.departure_window.start,
            end=mission.departure_window.end,
        ),
        passenger_count=mission.passenger_count,
        max_budget=budget,
        special_requirements=list(mission.special_requirements),
        status=mission.status,
    )


def _rfq_response(rfq: Rfq) -> BuyerRfqResponse:
    return BuyerRfqResponse(
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


def _approval_response(approval: ProcurementApproval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id.value,
        version=approval.version,
        mission_id=approval.mission_id.value,
        buyer_id=approval.buyer_id.value,
        quote_id=approval.quote_id.value,
        status=approval.status,
        approved_at=approval.approved_at,
        note=approval.note,
        supersedes_approval_id=(
            approval.supersedes_approval_id.value
            if approval.supersedes_approval_id is not None
            else None
        ),
        superseded_at=approval.superseded_at,
        consumed_at=approval.consumed_at,
        booking_id=approval.booking_id.value if approval.booking_id is not None else None,
    )


def _booking_response(booking: Booking) -> BookingStatusResponse:
    return BookingStatusResponse(
        id=booking.id.value,
        version=booking.version,
        mission_id=booking.mission_id.value,
        accepted_quote_id=booking.accepted_quote_id.value,
        operator_id=booking.operator_id.value,
        aircraft_id=booking.aircraft_id.value,
        state=booking.state,
        created_at=booking.created_at,
        state_changed_at=booking.state_changed_at,
    )


def _money_response(money: Money) -> MoneyResponse:
    return MoneyResponse(amount_minor=money.amount_minor, currency=str(money.currency))


@router.post("/missions", response_model=BuyerMissionResponse, status_code=status.HTTP_201_CREATED)
def create_mission(
    body: BuyerMissionCreate,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> BuyerMissionResponse:
    typed_buyer = OrganizationId(buyer_id)
    scope = f"POST:/v1/buyer-portal/missions:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        _portal(session).assert_buyer(typed_buyer)
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return BuyerMissionResponse.model_validate(stored.response_body)

        budget = None
        if body.max_budget is not None:
            budget = Money(
                body.max_budget.amount_minor,
                Currency(body.max_budget.currency.strip().upper()),
            )
        mission = _mission_service(session).create_mission(
            buyer_id=typed_buyer,
            origin_airport_id=AirportId(body.origin_airport_id),
            destination_airport_id=AirportId(body.destination_airport_id),
            departure_window=TimeRange(
                body.departure_window.start,
                body.departure_window.end,
            ),
            passenger_count=body.passenger_count,
            max_budget=budget,
            special_requirements=tuple(body.special_requirements),
            correlation_id=correlation_id,
        )
        response = _mission_response(mission)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/missions/{mission_id}", response_model=BuyerMissionResponse)
def get_mission(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
) -> BuyerMissionResponse:
    return _mission_response(
        _portal(session).mission(
            buyer_id=OrganizationId(buyer_id),
            mission_id=MissionId(mission_id),
        )
    )


@router.post("/missions/{mission_id}/open", response_model=BuyerMissionResponse)
def open_mission(
    mission_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> BuyerMissionResponse:
    typed_buyer = OrganizationId(buyer_id)
    scope = f"POST:/v1/buyer-portal/missions/{mission_id}/open:{buyer_id}"
    request_hash = canonical_request_hash({})
    with session.begin():
        _portal(session).mission(
            buyer_id=typed_buyer,
            mission_id=MissionId(mission_id),
        )
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return BuyerMissionResponse.model_validate(stored.response_body)
        mission = _mission_service(session).open_mission(
            mission_id=MissionId(mission_id),
            correlation_id=correlation_id,
        )
        response = _mission_response(mission)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/missions/{mission_id}/suppliers", response_model=SupplierSearchResponse)
def search_suppliers(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
    known_as_of: KnownAsOf = None,
    limit: SupplierLimit = 20,
) -> SupplierSearchResponse:
    cutoff = known_as_of or datetime.now(UTC)
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _portal(session).mission(
            buyer_id=OrganizationId(buyer_id),
            mission_id=MissionId(mission_id),
        )
        decision = _matching_service(session).match_mission(
            mission_id=MissionId(mission_id),
            known_as_of=cutoff,
        )

    suppliers: list[SupplierCandidateResponse] = []
    seen: set[UUID] = set()
    for match in decision.matches:
        operator_id = match.draft.operator_id.value
        if operator_id in seen:
            continue
        seen.add(operator_id)
        suppliers.append(
            SupplierCandidateResponse(
                supplier_rank=len(suppliers) + 1,
                operator_id=operator_id,
                best_aircraft_id=match.draft.aircraft_id.value,
                aircraft_type_id=match.draft.aircraft_type_id.value,
                matching_rank=match.rank,
                reason_codes=[reason.value for reason in match.draft.reason_codes],
                estimated_operating_cost=_money_response(match.draft.estimated_operating_cost),
                budget_comparison=match.draft.budget_comparison.value,
                budget_delta_minor=match.draft.budget_delta_minor,
                reposition_distance_nm=(
                    Decimal(match.draft.reposition_distance_tenths_nm) / Decimal(10)
                ).quantize(Decimal("0.1")),
                timing_buffer_minutes=match.draft.timing_buffer_minutes,
                schedule_risk_basis_points=match.draft.schedule_risk_basis_points,
                score_total_basis_points=match.score.total_basis_points,
                score_method=match.score.method,
            )
        )
        if len(suppliers) >= limit:
            break

    all_supplier_ids = {match.draft.operator_id.value for match in decision.matches}
    return SupplierSearchResponse(
        mission_id=mission_id,
        matching_policy_version=decision.policy_version,
        reference_currency=str(decision.reference_currency),
        known_as_of=decision.known_as_of,
        candidate_aircraft_count=decision.candidate_count,
        feasible_aircraft_count=decision.feasible_count,
        supplier_count=len(all_supplier_ids),
        returned_count=len(suppliers),
        suppliers=suppliers,
    )


@router.post(
    "/missions/{mission_id}/rfqs",
    response_model=BuyerRfqBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def issue_rfqs(
    mission_id: UUID,
    body: BuyerRfqIssue,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> BuyerRfqBatchResponse:
    if len(set(body.operator_ids)) != len(body.operator_ids):
        raise DomainValidationError("operator_ids cannot contain duplicates")

    typed_buyer = OrganizationId(buyer_id)
    scope = f"POST:/v1/buyer-portal/missions/{mission_id}/rfqs:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        _portal(session).mission(
            buyer_id=typed_buyer,
            mission_id=MissionId(mission_id),
        )
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return BuyerRfqBatchResponse.model_validate(stored.response_body)

        service = _rfq_service(session)
        rfqs = [
            service.create_and_send(
                mission_id=MissionId(mission_id),
                operator_id=OperatorId(operator_id),
                response_deadline=body.response_deadline,
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            )
            for operator_id in body.operator_ids
        ]
        response = BuyerRfqBatchResponse(
            mission_id=mission_id,
            issued_count=len(rfqs),
            rfqs=[_rfq_response(rfq) for rfq in rfqs],
        )
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get("/missions/{mission_id}/rfqs", response_model=BuyerRfqBatchResponse)
def list_rfqs(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
) -> BuyerRfqBatchResponse:
    _portal(session).mission(
        buyer_id=OrganizationId(buyer_id),
        mission_id=MissionId(mission_id),
    )
    items = _rfq_service(session).list_for_mission(MissionId(mission_id))
    return BuyerRfqBatchResponse(
        mission_id=mission_id,
        issued_count=len(items),
        rfqs=[_rfq_response(item) for item in items],
    )


@router.get(
    "/missions/{mission_id}/quotes/compare",
    response_model=BuyerQuoteComparisonResponse,
)
def compare_quotes(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
) -> BuyerQuoteComparisonResponse:
    evaluated_at = datetime.now(UTC)
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _portal(session).mission(
            buyer_id=OrganizationId(buyer_id),
            mission_id=MissionId(mission_id),
        )
        typed_mission = MissionId(mission_id)
        _visibility_policy(session).assert_mission_comparison_visible(typed_mission)
        comparison = _comparison_service(session).compare(
            mission_id=typed_mission,
            evaluated_at=evaluated_at,
        )

    entries = [
        QuoteComparisonEntryResponse(
            quote_id=entry.quote.id.value,
            operator_id=entry.operator_id.value,
            aircraft_id=entry.quote.aircraft_id.value,
            revision_number=entry.quote.revision_number,
            quote_status=entry.quote.status.value,
            valid_until=entry.quote.valid_until,
            currency=str(entry.normalization.currency),
            expected_total=_money_response(entry.normalization.expected_total),
            worst_case_total=_money_response(entry.normalization.worst_case_total),
            totals_complete=entry.normalization.totals_complete,
            pricing_confidence=entry.normalization.confidence.value,
            commercial_valid=entry.commercial_valid,
            aircraft_feasible=entry.aircraft_suitability.feasible,
            decision_eligible=entry.decision_eligible,
            eligibility_reasons=[reason.value for reason in entry.eligibility_reasons],
            rejection_reasons=[
                reason.value for reason in entry.aircraft_suitability.rejection_reasons
            ],
            currency_rank=entry.currency_rank,
            score_method=entry.score.method,
            score_total_basis_points=entry.score.total_basis_points,
        )
        for entry in comparison.entries
    ]
    return BuyerQuoteComparisonResponse(
        mission_id=mission_id,
        comparison_policy_version=comparison.comparison_policy_version,
        matching_policy_version=comparison.matching_policy_version,
        evaluated_at=comparison.evaluated_at,
        pricing_currencies=[str(currency) for currency in comparison.pricing_currencies],
        global_rank_available=comparison.global_rank_available,
        quotes=entries,
    )


@router.post(
    "/missions/{mission_id}/quotes/{quote_id}/approve",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def approve_quote(
    mission_id: UUID,
    quote_id: UUID,
    body: ApprovalRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> ApprovalResponse:
    typed_buyer = OrganizationId(buyer_id)
    typed_mission = MissionId(mission_id)
    scope = f"POST:/v1/buyer-portal/missions/{mission_id}/quotes/{quote_id}/approve:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        _portal(session).mission(buyer_id=typed_buyer, mission_id=typed_mission)
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return ApprovalResponse.model_validate(stored.response_body)

        _visibility_policy(session).assert_mission_comparison_visible(typed_mission)
        comparison = _comparison_service(session).compare(
            mission_id=typed_mission,
            evaluated_at=datetime.now(UTC),
        )
        selected = next(
            (entry for entry in comparison.entries if entry.quote.id.value == quote_id),
            None,
        )
        if selected is None:
            raise EntityConflictError("quote is not part of the current mission comparison")
        if not selected.decision_eligible:
            raise EntityConflictError(
                "quote is not currently eligible for procurement approval"
            )

        approval = _approval_service(session).approve(
            buyer_id=typed_buyer,
            mission_id=typed_mission,
            quote_id=QuoteId(quote_id),
            approved_at=datetime.now(UTC),
            note=body.note,
            correlation_id=correlation_id,
        )
        response = _approval_response(approval)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/approvals/{approval_id}/award",
    response_model=AwardResponse,
    status_code=status.HTTP_201_CREATED,
)
def award_approval(
    approval_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> AwardResponse:
    typed_buyer = OrganizationId(buyer_id)
    scope = f"POST:/v1/buyer-portal/approvals/{approval_id}/award:{buyer_id}"
    request_hash = canonical_request_hash({})
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
            return AwardResponse.model_validate(stored.response_body)

        approval, booking = _approval_service(session).award(
            buyer_id=typed_buyer,
            approval_id=ProcurementApprovalId(approval_id),
            awarded_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        response = AwardResponse(
            approval=_approval_response(approval),
            booking=_booking_response(booking),
        )
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get(
    "/missions/{mission_id}/booking",
    response_model=BookingStatusResponse,
)
def booking_status(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
) -> BookingStatusResponse:
    booking = _portal(session).booking_for_mission(
        buyer_id=OrganizationId(buyer_id),
        mission_id=MissionId(mission_id),
    )
    return _booking_response(booking)


@router.get(
    "/missions/{mission_id}/audit",
    response_model=AuditTrailResponse,
)
def audit_trail(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdDep,
    limit: AuditLimit = 100,
) -> AuditTrailResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = _portal(session).audit(
            buyer_id=OrganizationId(buyer_id),
            mission_id=MissionId(mission_id),
            limit=limit,
        )
    events = [
        AuditEventResponse(
            event_id=item.event_id,
            aggregate_type=item.aggregate_type,
            aggregate_id=item.aggregate_id,
            aggregate_version=item.aggregate_version,
            event_type=item.event_type,
            event_version=item.event_version,
            occurred_at=item.occurred_at,
            recorded_at=item.recorded_at,
            actor_id=item.actor_id,
            correlation_id=item.correlation_id,
            causation_id=item.causation_id,
        )
        for item in page.items
    ]
    return AuditTrailResponse(
        mission_id=mission_id,
        returned_count=len(events),
        truncated=page.truncated,
        events=events,
    )
