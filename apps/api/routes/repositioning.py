from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_session
from charteros.application.graph_queries import GraphQueryService
from charteros.application.repositioning import RepositioningService
from charteros.infrastructure.db.repositories import (
    SqlAlchemyAirportRepository,
    SqlAlchemyGraphQueryRepository,
    SqlAlchemyMatchingSnapshotRepository,
    SqlAlchemyRepositionOpportunityRepository,
)
from charteros.repositioning import (
    CurrencyOptimizationPlan,
    RepositionAssignment,
    RepositionOptimization,
)

router = APIRouter(prefix="/v1/optimization", tags=["repositioning"])
SessionDep = Annotated[Session, Depends(get_session)]
EmptyLegLimit = Annotated[int, Query(ge=1, le=100)]
OpportunityLimit = Annotated[int, Query(ge=1, le=2000)]


class RepositionAssignmentResponse(BaseModel):
    aircraft_id: UUID
    operator_id: UUID
    previous_booking_id: UUID
    next_booking_id: UUID
    mission_id: UUID
    quote_id: UUID
    from_airport_id: UUID
    mission_origin_airport_id: UUID
    mission_destination_airport_id: UUID
    continuity_airport_id: UUID
    window_start: datetime
    window_end: datetime
    aircraft_available_at: datetime
    scheduled_departure: datetime
    continuity_ready_at: datetime
    previous_revenue_distance_nm: Decimal
    previous_revenue_minutes: int
    baseline_reposition_distance_nm: Decimal
    pre_reposition_distance_nm: Decimal
    revenue_distance_nm: Decimal
    post_reposition_distance_nm: Decimal
    baseline_reposition_cost_minor: int
    reposition_cost_minor: int
    revenue_leg_operating_cost_minor: int
    revenue_minor: int
    gross_margin_minor: int
    opportunity_cost_minor: int
    margin_minor: int
    currency: str
    pricing_confidence: str
    totals_complete: bool


class CurrencyOptimizationPlanResponse(BaseModel):
    currency: str
    candidate_count: int
    assignment_count: int
    total_margin_minor: int
    assignments: list[RepositionAssignmentResponse]


class RepositionOptimizationResponse(BaseModel):
    policy_version: str
    projection_version: int
    evaluated_at: datetime
    window_start: datetime
    window_end: datetime
    structural_empty_leg_count: int
    feasible_empty_leg_count: int
    quoted_future_leg_count: int
    feasible_candidate_count: int
    rejection_summary: dict[str, int]
    global_plan_available: bool
    currency_plans: list[CurrencyOptimizationPlanResponse]


def _service(session: Session) -> RepositioningService:
    return RepositioningService(
        graph=GraphQueryService(SqlAlchemyGraphQueryRepository(session)),
        airports=SqlAlchemyAirportRepository(session),
        snapshots=SqlAlchemyMatchingSnapshotRepository(session),
        opportunities=SqlAlchemyRepositionOpportunityRepository(session),
    )


def _distance(tenths_nm: int) -> Decimal:
    return Decimal(tenths_nm) / Decimal(10)


def _assignment_response(item: RepositionAssignment) -> RepositionAssignmentResponse:
    return RepositionAssignmentResponse(
        aircraft_id=item.aircraft_id.value,
        operator_id=item.operator_id.value,
        previous_booking_id=item.previous_booking_id,
        next_booking_id=item.next_booking_id,
        mission_id=item.mission_id.value,
        quote_id=item.quote_id.value,
        from_airport_id=item.from_airport_id,
        mission_origin_airport_id=item.mission_origin_airport_id.value,
        mission_destination_airport_id=item.mission_destination_airport_id.value,
        continuity_airport_id=item.continuity_airport_id,
        window_start=item.window_start,
        window_end=item.window_end,
        aircraft_available_at=item.aircraft_available_at,
        scheduled_departure=item.scheduled_departure,
        continuity_ready_at=item.continuity_ready_at,
        previous_revenue_distance_nm=_distance(item.previous_revenue_distance_tenths_nm),
        previous_revenue_minutes=item.previous_revenue_minutes,
        baseline_reposition_distance_nm=_distance(item.baseline_reposition_distance_tenths_nm),
        pre_reposition_distance_nm=_distance(item.pre_reposition_distance_tenths_nm),
        revenue_distance_nm=_distance(item.revenue_distance_tenths_nm),
        post_reposition_distance_nm=_distance(item.post_reposition_distance_tenths_nm),
        baseline_reposition_cost_minor=item.baseline_reposition_cost.amount_minor,
        reposition_cost_minor=item.reposition_cost.amount_minor,
        revenue_leg_operating_cost_minor=item.revenue_leg_operating_cost.amount_minor,
        revenue_minor=item.revenue.amount_minor,
        gross_margin_minor=item.gross_margin.amount_minor,
        opportunity_cost_minor=item.opportunity_cost.amount_minor,
        margin_minor=item.margin.amount_minor,
        currency=str(item.currency),
        pricing_confidence=item.pricing_confidence.value,
        totals_complete=item.totals_complete,
    )


def _plan_response(item: CurrencyOptimizationPlan) -> CurrencyOptimizationPlanResponse:
    return CurrencyOptimizationPlanResponse(
        currency=str(item.currency),
        candidate_count=item.candidate_count,
        assignment_count=item.assignment_count,
        total_margin_minor=item.total_margin.amount_minor,
        assignments=[_assignment_response(value) for value in item.assignments],
    )


def _response(value: RepositionOptimization) -> RepositionOptimizationResponse:
    return RepositionOptimizationResponse(
        policy_version=value.policy_version,
        projection_version=value.projection_version,
        evaluated_at=value.evaluated_at,
        window_start=value.window_start,
        window_end=value.window_end,
        structural_empty_leg_count=value.structural_empty_leg_count,
        feasible_empty_leg_count=value.feasible_empty_leg_count,
        quoted_future_leg_count=value.quoted_future_leg_count,
        feasible_candidate_count=value.feasible_candidate_count,
        rejection_summary={
            reason.value: count for reason, count in value.rejection_summary.items()
        },
        global_plan_available=value.global_plan_available,
        currency_plans=[_plan_response(item) for item in value.currency_plans],
    )


@router.get("/repositioning", response_model=RepositionOptimizationResponse)
def optimize_repositioning(
    session: SessionDep,
    window_start: datetime,
    window_end: datetime,
    evaluated_at: datetime,
    empty_leg_limit: EmptyLegLimit = 100,
    opportunity_limit: OpportunityLimit = 2000,
) -> RepositionOptimizationResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        result = _service(session).optimize(
            window_start=window_start,
            window_end=window_end,
            evaluated_at=evaluated_at,
            empty_leg_limit=empty_leg_limit,
            opportunity_limit=opportunity_limit,
        )
    return _response(result)
