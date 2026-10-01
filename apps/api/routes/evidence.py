from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_session
from charteros.application.evidence import (
    EvidencePackage,
    EvidenceParty,
    EvidenceService,
    EvidenceSubjectType,
)
from charteros.application.exceptions import EntityConflictError
from charteros.infrastructure.db.evidence_integrity import assert_evidence_integrity
from charteros.infrastructure.db.repositories.evidence import SqlAlchemyEvidenceRepository
from charteros.observability import get_operational_metrics

router = APIRouter(prefix="/v1/evidence", tags=["audit-evidence"])

SessionDep = Annotated[Session, Depends(get_session)]
BuyerIdOptional = Annotated[UUID | None, Header(alias="X-Buyer-Id")]
OperatorIdOptional = Annotated[UUID | None, Header(alias="X-Operator-Id")]
EventLimit = Annotated[int, Query(ge=1, le=500)]


class EvidencePackageResponse(BaseModel):
    schema_version: str
    subject_type: str
    subject_id: UUID
    canonical_cutoff: datetime | None
    generated_at: datetime
    sources: list[dict[str, object]]
    events: list[dict[str, object]]
    decisions: list[dict[str, object]]
    policy_versions: list[str]
    completeness: str
    diagnostics: list[str]
    integrity_digest: str


def _party(buyer_id: UUID | None, operator_id: UUID | None) -> EvidenceParty:
    return EvidenceParty(buyer_id=buyer_id, operator_id=operator_id)


def _response(package: EvidencePackage) -> EvidencePackageResponse:
    return EvidencePackageResponse.model_validate(package.to_dict())


def _build(
    *,
    session: Session,
    subject_type: EvidenceSubjectType,
    subject_id: UUID,
    buyer_id: UUID | None,
    operator_id: UUID | None,
    limit: int,
) -> EvidencePackageResponse:
    metrics = get_operational_metrics()
    started = perf_counter()
    try:
        with session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            try:
                assert_evidence_integrity(session)
            except EntityConflictError:
                metrics.evidence_integrity_failure()
                raise
            package = EvidenceService(SqlAlchemyEvidenceRepository(session)).build(
                subject_type=subject_type,
                subject_id=subject_id,
                party=_party(buyer_id, operator_id),
                event_limit=limit,
            )
    except EntityConflictError:
        metrics.evidence_build(
            subject_type=subject_type.value,
            outcome="failure",
            completeness="unknown",
            duration_seconds=perf_counter() - started,
        )
        raise

    metrics.evidence_build(
        subject_type=subject_type.value,
        outcome="success",
        completeness=package.completeness.value,
        duration_seconds=perf_counter() - started,
    )
    return _response(package)


@router.get("/missions/{mission_id}", response_model=EvidencePackageResponse)
def mission_evidence(
    mission_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: EventLimit = 250,
) -> EvidencePackageResponse:
    return _build(
        session=session,
        subject_type=EvidenceSubjectType.MISSION,
        subject_id=mission_id,
        buyer_id=buyer_id,
        operator_id=operator_id,
        limit=limit,
    )


@router.get("/bookings/{booking_id}", response_model=EvidencePackageResponse)
def booking_evidence(
    booking_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: EventLimit = 250,
) -> EvidencePackageResponse:
    return _build(
        session=session,
        subject_type=EvidenceSubjectType.BOOKING,
        subject_id=booking_id,
        buyer_id=buyer_id,
        operator_id=operator_id,
        limit=limit,
    )


@router.get("/disruptions/{disruption_id}", response_model=EvidencePackageResponse)
def disruption_evidence(
    disruption_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: EventLimit = 250,
) -> EvidencePackageResponse:
    return _build(
        session=session,
        subject_type=EvidenceSubjectType.DISRUPTION,
        subject_id=disruption_id,
        buyer_id=buyer_id,
        operator_id=operator_id,
        limit=limit,
    )


@router.get(
    "/reconciliations/{reconciliation_id}",
    response_model=EvidencePackageResponse,
)
def reconciliation_evidence(
    reconciliation_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: EventLimit = 250,
) -> EvidencePackageResponse:
    return _build(
        session=session,
        subject_type=EvidenceSubjectType.RECONCILIATION,
        subject_id=reconciliation_id,
        buyer_id=buyer_id,
        operator_id=operator_id,
        limit=limit,
    )
