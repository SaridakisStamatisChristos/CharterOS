from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_session
from charteros.application.matching import MatchingService
from charteros.domain.missions import MissionId
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyAirportRepository
from charteros.infrastructure.db.repositories.matching import SqlAlchemyMatchingSnapshotRepository
from charteros.infrastructure.db.repositories.missions import SqlAlchemyMissionRepository
from charteros.matching import BudgetComparison, MatchingDecision, MatchReasonCode, RankedMatch
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1", tags=["matching"])
ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
KnownAsOf = Annotated[datetime | None, Query(description="UTC knowledge-time cutoff for replay")]
ResultLimit = Annotated[int, Query(ge=1, le=100)]


class MoneyResponse(BaseModel):
    amount_minor: int
    currency: str


class PositionEvidenceResponse(BaseModel):
    id: UUID
    airport_id: UUID | None
    latitude: Decimal
    longitude: Decimal
    event_time: datetime
    recorded_at: datetime
    source: str
    provenance: dict[str, object]


class AvailabilityEvidenceResponse(BaseModel):
    id: UUID
    valid_from: datetime
    valid_to: datetime
    status: str
    recorded_at: datetime
    source: str
    provenance: dict[str, object]


class ReferenceProfileResponse(BaseModel):
    id: UUID
    aircraft_type_id: UUID
    cruise_speed_kts: int
    operating_cost_per_hour: MoneyResponse
    max_reposition_nm: int
    turnaround_buffer_minutes: int
    source: str
    provenance: dict[str, object]
    recorded_at: datetime


class ScoreResponse(BaseModel):
    method: str
    total_basis_points: int
    deadhead_points: int
    operating_cost_points: int
    timing_buffer_points: int
    schedule_risk_points: int


class MatchResponse(BaseModel):
    rank: int
    aircraft_id: UUID
    operator_id: UUID
    aircraft_type_id: UUID
    reason_codes: list[MatchReasonCode]
    route_distance_nm: Decimal
    required_range_nm: int
    reposition_distance_nm: Decimal
    route_minutes: int
    reposition_minutes: int
    timing_buffer_minutes: int
    schedule_risk_basis_points: int
    estimated_operating_cost: MoneyResponse
    budget_comparison: BudgetComparison
    budget_delta_minor: int | None
    position: PositionEvidenceResponse
    availability: AvailabilityEvidenceResponse
    reference_profile: ReferenceProfileResponse
    score: ScoreResponse


class MatchingResponse(BaseModel):
    mission_id: UUID
    policy_version: str
    reference_currency: str
    known_as_of: datetime
    position_event_cutoff: datetime
    route_distance_nm: Decimal
    required_range_nm: int
    candidate_count: int
    feasible_count: int
    returned_count: int
    rejection_summary: dict[str, int]
    matches: list[MatchResponse]


def _nm(tenths: int) -> Decimal:
    return (Decimal(tenths) / Decimal(10)).quantize(Decimal("0.1"))


def _match_response(item: RankedMatch) -> MatchResponse:
    draft = item.draft
    profile = draft.reference_profile
    position = draft.position
    availability = draft.availability
    return MatchResponse(
        rank=item.rank,
        aircraft_id=draft.aircraft_id.value,
        operator_id=draft.operator_id.value,
        aircraft_type_id=draft.aircraft_type_id.value,
        reason_codes=list(draft.reason_codes),
        route_distance_nm=_nm(draft.route_distance_tenths_nm),
        required_range_nm=draft.required_range_nm,
        reposition_distance_nm=_nm(draft.reposition_distance_tenths_nm),
        route_minutes=draft.route_minutes,
        reposition_minutes=draft.reposition_minutes,
        timing_buffer_minutes=draft.timing_buffer_minutes,
        schedule_risk_basis_points=draft.schedule_risk_basis_points,
        estimated_operating_cost=MoneyResponse(
            amount_minor=draft.estimated_operating_cost.amount_minor,
            currency=str(draft.estimated_operating_cost.currency),
        ),
        budget_comparison=draft.budget_comparison,
        budget_delta_minor=draft.budget_delta_minor,
        position=PositionEvidenceResponse(
            id=position.id.value,
            airport_id=position.airport_id.value if position.airport_id else None,
            latitude=position.latitude,
            longitude=position.longitude,
            event_time=position.event_time,
            recorded_at=position.recorded_at,
            source=position.source,
            provenance=dict(position.provenance),
        ),
        availability=AvailabilityEvidenceResponse(
            id=availability.id.value,
            valid_from=availability.interval.start,
            valid_to=availability.interval.end,
            status=availability.status.value,
            recorded_at=availability.recorded_at,
            source=availability.source,
            provenance=dict(availability.provenance),
        ),
        reference_profile=ReferenceProfileResponse(
            id=profile.id.value,
            aircraft_type_id=profile.aircraft_type_id.value,
            cruise_speed_kts=profile.cruise_speed_kts,
            operating_cost_per_hour=MoneyResponse(
                amount_minor=profile.operating_cost_per_hour.amount_minor,
                currency=str(profile.operating_cost_per_hour.currency),
            ),
            max_reposition_nm=profile.max_reposition_nm,
            turnaround_buffer_minutes=profile.turnaround_buffer_minutes,
            source=profile.source,
            provenance=dict(profile.provenance),
            recorded_at=profile.recorded_at,
        ),
        score=ScoreResponse(
            method=item.score.method,
            total_basis_points=item.score.total_basis_points,
            deadhead_points=item.score.deadhead_points,
            operating_cost_points=item.score.operating_cost_points,
            timing_buffer_points=item.score.timing_buffer_points,
            schedule_risk_points=item.score.schedule_risk_points,
        ),
    )


def _response(mission_id: UUID, decision: MatchingDecision, limit: int) -> MatchingResponse:
    matches = [_match_response(item) for item in decision.matches[:limit]]
    return MatchingResponse(
        mission_id=mission_id,
        policy_version=decision.policy_version,
        reference_currency=str(decision.reference_currency),
        known_as_of=decision.known_as_of,
        position_event_cutoff=decision.position_event_cutoff,
        route_distance_nm=_nm(decision.route_distance_tenths_nm),
        required_range_nm=decision.required_range_nm,
        candidate_count=decision.candidate_count,
        feasible_count=decision.feasible_count,
        returned_count=len(matches),
        rejection_summary={
            reason.value: count for reason, count in decision.rejection_summary.items()
        },
        matches=matches,
    )


@router.get("/missions/{mission_id}/matches", response_model=MatchingResponse)
def get_mission_matches(
    mission_id: UUID,
    session: SessionDep,
    clock: ClockDep,
    known_as_of: KnownAsOf = None,
    limit: ResultLimit = 20,
) -> MatchingResponse:
    cutoff = known_as_of or clock.now()
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        decision = MatchingService(
            missions=SqlAlchemyMissionRepository(session),
            airports=SqlAlchemyAirportRepository(session),
            snapshots=SqlAlchemyMatchingSnapshotRepository(session),
        ).match_mission(mission_id=MissionId(mission_id), known_as_of=cutoff)
    return _response(mission_id, decision, limit)
