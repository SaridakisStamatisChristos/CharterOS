from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.application.data_governance import (
    DATA_ASSET_POLICIES,
    DATA_GOVERNANCE_POLICY_VERSION,
    EXTERNAL_DATA_ASSET_POLICIES,
    TENANT_DEPENDENCY_GRAPH,
    TENANT_EXPORT_SCHEMA_VERSION,
    DataGovernanceService,
    LegalHold,
    LifecycleOutcome,
    TenantKind,
)
from charteros.application.evidence import EvidenceParty, EvidenceService, EvidenceSubjectType
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.idempotency import canonical_request_hash
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.evidence_integrity import assert_evidence_integrity
from charteros.infrastructure.db.repositories.evidence import SqlAlchemyEvidenceRepository
from charteros.infrastructure.db.repositories.governance import SqlAlchemyDataGovernanceRepository
from charteros.security.auth import AuthenticatedPrincipal
from charteros.shared.clock import Clock

router = APIRouter(prefix="/v1/governance", tags=["data-governance"])

SessionDep = Annotated[Session, Depends(get_session)]
ClockDep = Annotated[Clock, Depends(get_clock)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
GovernanceKeyDep = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
]
MissionLimit = Annotated[int, Query(ge=1, le=100)]
EventLimit = Annotated[int, Query(ge=1, le=500)]


class LegalHoldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)


class LegalHoldRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)


class LegalHoldResponse(BaseModel):
    id: UUID
    tenant_kind: TenantKind
    tenant_id: UUID
    reason: str
    status: str
    created_at: datetime
    released_at: datetime | None
    release_reason: str | None


class LifecycleResponse(BaseModel):
    id: UUID
    tenant_kind: TenantKind
    tenant_id: UUID
    operation: str
    status: str
    policy_version: str
    report: dict[str, object]
    requested_at: datetime
    completed_at: datetime


class TenantExportResponse(BaseModel):
    schema_version: str
    policy_version: str
    tenant_kind: TenantKind
    tenant_id: UUID
    generated_at: datetime
    source_provenance: list[str]
    master_data: dict[str, object]
    mission_evidence: list[dict[str, object]]
    excluded_surfaces: list[str]
    verification_digest: str


def _principal(request: Request) -> AuthenticatedPrincipal:
    return cast(AuthenticatedPrincipal, request.state.authenticated_principal)


def _actor_digest(request: Request) -> str:
    principal = _principal(request)
    material = f"{principal.issuer}\x00{principal.subject}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _request_key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _hold_response(hold: LegalHold) -> LegalHoldResponse:
    return LegalHoldResponse(
        id=hold.id,
        tenant_kind=hold.tenant_kind,
        tenant_id=hold.tenant_id,
        reason=hold.reason,
        status=hold.status,
        created_at=hold.created_at,
        released_at=hold.released_at,
        release_reason=hold.release_reason,
    )


def _lifecycle_response(outcome: LifecycleOutcome) -> LifecycleResponse:
    return LifecycleResponse(
        id=outcome.id,
        tenant_kind=outcome.tenant_kind,
        tenant_id=outcome.tenant_id,
        operation=outcome.operation.value,
        status=outcome.status.value,
        policy_version=outcome.policy_version,
        report=outcome.report,
        requested_at=outcome.requested_at,
        completed_at=outcome.completed_at,
    )


def _canonical_json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"unsupported canonical export value: {type(value).__name__}")


def _export_digest(document: dict[str, object]) -> str:
    payload = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_canonical_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@router.get("/policy")
def data_governance_policy() -> dict[str, object]:
    return {
        "policy_version": DATA_GOVERNANCE_POLICY_VERSION,
        "classes": {
            "A": "immutable transactional/evidence record",
            "B": "tenant business master data",
            "C": "personal-data capable field/surface",
            "D": "operational telemetry/log state",
            "E": "derived/rebuildable state",
            "F": "security/audit data",
            "G": "legal-hold/control-plane scope",
        },
        "database_assets": [
            {
                "table": item.table,
                "classes": sorted(value.value for value in item.classes),
                "retention_action": item.retention_action.value,
                "exportable": item.exportable,
                "legal_hold_applicable": item.legal_hold_applicable,
                "automated_delete_allowed": item.automated_delete_allowed,
                "rationale": item.rationale,
            }
            for item in DATA_ASSET_POLICIES
        ],
        "external_assets": [
            {
                "asset": item.asset,
                "classes": sorted(value.value for value in item.classes),
                "retention_action": item.retention_action.value,
                "exportable": item.exportable,
                "legal_hold_applicable": item.legal_hold_applicable,
                "rationale": item.rationale,
            }
            for item in EXTERNAL_DATA_ASSET_POLICIES
        ],
        "tenant_dependencies": [
            {
                "tenant_kind": item.tenant_kind.value,
                "table": item.table,
                "relationship": item.relationship,
                "evidence_blocker": item.evidence_blocker,
                "rationale": item.rationale,
            }
            for item in TENANT_DEPENDENCY_GRAPH
        ],
    }


@router.post(
    "/tenants/{tenant_kind}/{tenant_id}/legal-holds",
    response_model=LegalHoldResponse,
)
def create_legal_hold(
    tenant_kind: TenantKind,
    tenant_id: UUID,
    body: LegalHoldCreate,
    session: SessionDep,
    clock: ClockDep,
    request: Request,
) -> LegalHoldResponse:
    with session.begin():
        hold = DataGovernanceService(SqlAlchemyDataGovernanceRepository(session)).create_legal_hold(
            tenant_kind=tenant_kind,
            tenant_id=tenant_id,
            reason=body.reason,
            actor_subject_digest=_actor_digest(request),
            recorded_at=clock.now(),
        )
    return _hold_response(hold)


@router.get(
    "/tenants/{tenant_kind}/{tenant_id}/legal-holds",
    response_model=list[LegalHoldResponse],
)
def list_legal_holds(
    tenant_kind: TenantKind,
    tenant_id: UUID,
    session: SessionDep,
) -> list[LegalHoldResponse]:
    repository = SqlAlchemyDataGovernanceRepository(session)
    if not repository.tenant_exists(tenant_kind, tenant_id):
        raise EntityNotFoundError("tenant does not exist")
    return [_hold_response(hold) for hold in repository.list_legal_holds(tenant_kind, tenant_id)]


@router.post("/legal-holds/{hold_id}/release", response_model=LegalHoldResponse)
def release_legal_hold(
    hold_id: UUID,
    body: LegalHoldRelease,
    session: SessionDep,
    clock: ClockDep,
    request: Request,
) -> LegalHoldResponse:
    with session.begin():
        repository = SqlAlchemyDataGovernanceRepository(session)
        hold = DataGovernanceService(repository).release_legal_hold(
            hold_id=hold_id,
            reason=body.reason,
            actor_subject_digest=_actor_digest(request),
            recorded_at=clock.now(),
        )
    return _hold_response(hold)


@router.post(
    "/tenants/{tenant_kind}/{tenant_id}/close",
    response_model=LifecycleResponse,
)
def close_tenant(
    tenant_kind: TenantKind,
    tenant_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    governance_key: GovernanceKeyDep,
    clock: ClockDep,
    request: Request,
) -> LifecycleResponse:
    request_hash = canonical_request_hash(
        {"operation": "closure", "tenant_kind": tenant_kind.value, "tenant_id": str(tenant_id)}
    )
    with session.begin():
        outcome = DataGovernanceService(SqlAlchemyDataGovernanceRepository(session)).close_tenant(
            tenant_kind=tenant_kind,
            tenant_id=tenant_id,
            request_key_digest=_request_key_digest(governance_key),
            request_hash=request_hash,
            actor_subject_digest=_actor_digest(request),
            recorded_at=clock.now(),
            correlation_id=correlation_id,
        )
    return _lifecycle_response(outcome)


@router.post(
    "/tenants/{tenant_kind}/{tenant_id}/erase",
    response_model=LifecycleResponse,
)
def erase_tenant(
    tenant_kind: TenantKind,
    tenant_id: UUID,
    session: SessionDep,
    governance_key: GovernanceKeyDep,
    clock: ClockDep,
    request: Request,
) -> LifecycleResponse:
    request_hash = canonical_request_hash(
        {"operation": "erasure", "tenant_kind": tenant_kind.value, "tenant_id": str(tenant_id)}
    )
    with session.begin():
        outcome = DataGovernanceService(SqlAlchemyDataGovernanceRepository(session)).erase_tenant(
            tenant_kind=tenant_kind,
            tenant_id=tenant_id,
            request_key_digest=_request_key_digest(governance_key),
            request_hash=request_hash,
            actor_subject_digest=_actor_digest(request),
            recorded_at=clock.now(),
        )
    return _lifecycle_response(outcome)


@router.get(
    "/tenants/{tenant_kind}/{tenant_id}/export",
    response_model=TenantExportResponse,
)
def export_tenant_data(
    tenant_kind: TenantKind,
    tenant_id: UUID,
    session: SessionDep,
    clock: ClockDep,
    max_missions: MissionLimit = 100,
    event_limit: EventLimit = 250,
) -> TenantExportResponse:
    repository = SqlAlchemyDataGovernanceRepository(session)
    generated_at = clock.now()
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        assert_evidence_integrity(session)
        if not repository.tenant_exists(tenant_kind, tenant_id):
            raise EntityNotFoundError("tenant does not exist")
        master_data = repository.export_master_data(tenant_kind, tenant_id)
        mission_ids = repository.mission_ids_for_tenant(
            tenant_kind, tenant_id, limit=max_missions + 1
        )
        if len(mission_ids) > max_missions:
            raise EntityConflictError(
                "tenant export exceeds the requested mission bound; narrow or paginate the export"
            )

        party = EvidenceParty(
            buyer_id=tenant_id if tenant_kind is TenantKind.BUYER else None,
            operator_id=tenant_id if tenant_kind is TenantKind.OPERATOR else None,
        )
        evidence_service = EvidenceService(SqlAlchemyEvidenceRepository(session))
        mission_evidence: list[dict[str, object]] = []
        for mission_id in mission_ids:
            package = evidence_service.build(
                subject_type=EvidenceSubjectType.MISSION,
                subject_id=mission_id,
                party=party,
                event_limit=event_limit,
            )
            document = package.to_dict()
            if document.get("completeness") != "complete":
                raise EntityConflictError(
                    "tenant export would be incomplete at the configured event bound"
                )
            mission_evidence.append(cast(dict[str, object], document))

    excluded = [
        "authentication_security_logs",
        "application_logs",
        "idempotency_records",
        "api_rate_limit_windows",
        "outbox_delivery_internals",
        "evidence_integrity_internal_ledger",
    ]
    source_provenance = [
        "canonical PostgreSQL tenant master data",
        "decision/audit evidence reconstructed through EvidenceService",
        "evidence-integrity verification performed before export",
    ]
    digest_document: dict[str, object] = {
        "schema_version": TENANT_EXPORT_SCHEMA_VERSION,
        "policy_version": DATA_GOVERNANCE_POLICY_VERSION,
        "tenant_kind": tenant_kind.value,
        "tenant_id": str(tenant_id),
        "master_data": master_data,
        "mission_evidence": mission_evidence,
        "excluded_surfaces": excluded,
        "source_provenance": source_provenance,
    }
    return TenantExportResponse(
        schema_version=TENANT_EXPORT_SCHEMA_VERSION,
        policy_version=DATA_GOVERNANCE_POLICY_VERSION,
        tenant_kind=tenant_kind,
        tenant_id=tenant_id,
        generated_at=generated_at,
        source_provenance=source_provenance,
        master_data=master_data,
        mission_evidence=mission_evidence,
        excluded_surfaces=excluded,
        verification_digest=_export_digest(digest_document),
    )
