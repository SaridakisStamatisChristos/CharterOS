from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.domain.shared.ids import CorrelationId

DATA_GOVERNANCE_POLICY_VERSION = "data-governance-v1"
TENANT_EXPORT_SCHEMA_VERSION = "tenant-data-export-v1"


class DataClass(StrEnum):
    IMMUTABLE_EVIDENCE = "A"
    TENANT_BUSINESS_MASTER = "B"
    PERSONAL_DATA = "C"
    OPERATIONAL_TELEMETRY = "D"
    DERIVED_REBUILDABLE = "E"
    SECURITY_AUDIT = "F"
    LEGAL_HOLD = "G"


class RetentionAction(StrEnum):
    PRESERVE = "preserve"
    CLOSE_OR_REVIEW = "close-or-review"
    POLICY_TTL_DELETE = "policy-ttl-delete"
    REBUILDABLE_PURGE = "rebuildable-purge"
    APPEND_ONLY_AUDIT = "append-only-audit"


class TenantKind(StrEnum):
    BUYER = "buyer"
    OPERATOR = "operator"


class LifecycleOperation(StrEnum):
    CLOSURE = "closure"
    ERASURE = "erasure"


class LifecycleStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DataAssetPolicy:
    table: str
    classes: frozenset[DataClass]
    retention_action: RetentionAction
    exportable: bool
    legal_hold_applicable: bool
    automated_delete_allowed: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class ExternalDataAssetPolicy:
    asset: str
    classes: frozenset[DataClass]
    retention_action: RetentionAction
    exportable: bool
    legal_hold_applicable: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class TenantDependency:
    tenant_kind: TenantKind
    table: str
    relationship: str
    evidence_blocker: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class LegalHold:
    id: UUID
    tenant_kind: TenantKind
    tenant_id: UUID
    reason: str
    status: str
    created_at: datetime
    released_at: datetime | None
    release_reason: str | None


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    id: UUID
    tenant_kind: TenantKind
    tenant_id: UUID
    operation: LifecycleOperation
    status: LifecycleStatus
    policy_version: str
    report: dict[str, object]
    requested_at: datetime
    completed_at: datetime
    request_hash: str


def _asset(
    table: str,
    classes: tuple[DataClass, ...],
    action: RetentionAction,
    *,
    exportable: bool = False,
    hold: bool = True,
    delete: bool = False,
    rationale: str,
) -> DataAssetPolicy:
    return DataAssetPolicy(
        table=table,
        classes=frozenset(classes),
        retention_action=action,
        exportable=exportable,
        legal_hold_applicable=hold,
        automated_delete_allowed=delete,
        rationale=rationale,
    )


_EVIDENCE_EXPORT = (
    "aircraft_position_observations",
    "aircraft_availability_records",
    "missions",
    "rfqs",
    "quotes",
    "quote_price_components",
    "bookings",
    "aircraft_capacity_reservations",
    "contracts",
    "tenders",
    "tender_invitations",
    "procurement_approvals",
    "disruptions",
    "disruption_proposals",
    "disruption_commercial_changes",
    "disruption_buyer_decisions",
    "financial_reconciliations",
    "operator_invoice_revisions",
    "operator_invoice_lines",
    "reconciliation_disputes",
    "reconciliation_variance_approvals",
    "fx_locks",
    "fx_lock_conversions",
    "decision_evidence_snapshots",
)
_PERSONAL_CAPABLE = frozenset(
    {
        "organizations",
        "operators",
        "missions",
        "rfqs",
        "quotes",
        "quote_price_components",
        "contracts",
        "disruptions",
        "disruption_proposals",
        "disruption_commercial_changes",
        "disruption_buyer_decisions",
        "operator_invoice_revisions",
        "operator_invoice_lines",
        "reconciliation_disputes",
        "reconciliation_variance_approvals",
        "decision_evidence_snapshots",
        "outbox_events",
        "idempotency_records",
        "charter_graph_nodes",
        "charter_graph_edges",
        "data_governance_legal_holds",
    }
)
_APPEND_ONLY = frozenset(
    {
        "tender_admin_corrections",
        "operator_invoice_revisions",
        "operator_invoice_lines",
        "reconciliation_disputes",
        "reconciliation_variance_approvals",
        "disruption_buyer_decisions",
        "fx_rates",
        "fx_lock_conversions",
        "decision_evidence_snapshots",
        "outbox_events",
        "evidence_integrity_entries",
        "evidence_integrity_checkpoints",
        "data_governance_lifecycle_operations",
        "data_governance_events",
    }
)

DATA_ASSET_POLICIES: tuple[DataAssetPolicy, ...] = (
    _asset(
        "organizations",
        (DataClass.TENANT_BUSINESS_MASTER, DataClass.PERSONAL_DATA),
        RetentionAction.CLOSE_OR_REVIEW,
        exportable=True,
        rationale="Tenant legal/trading identity; may identify a sole trader or contact context.",
    ),
    _asset(
        "operators",
        (DataClass.TENANT_BUSINESS_MASTER, DataClass.PERSONAL_DATA),
        RetentionAction.CLOSE_OR_REVIEW,
        exportable=True,
        rationale="Operator master data and document references; closure is safer than blind deletion.",
    ),
    _asset(
        "airports",
        (DataClass.TENANT_BUSINESS_MASTER,),
        RetentionAction.PRESERVE,
        hold=False,
        rationale="Shared aviation reference data, not tenant-owned subject data.",
    ),
    _asset(
        "aircraft_types",
        (DataClass.TENANT_BUSINESS_MASTER,),
        RetentionAction.PRESERVE,
        hold=False,
        rationale="Shared aircraft reference data required by authoritative records.",
    ),
    _asset(
        "aircraft",
        (DataClass.TENANT_BUSINESS_MASTER,),
        RetentionAction.CLOSE_OR_REVIEW,
        exportable=True,
        rationale="Operator fleet master referenced by commercial and operational evidence.",
    ),
    _asset(
        "matching_reference_profiles",
        (DataClass.IMMUTABLE_EVIDENCE,),
        RetentionAction.PRESERVE,
        rationale="Reference evidence required to reproduce historical feasibility decisions.",
    ),
    *tuple(
        _asset(
            table,
            (
                (DataClass.IMMUTABLE_EVIDENCE, DataClass.PERSONAL_DATA)
                if table in _PERSONAL_CAPABLE
                else (DataClass.IMMUTABLE_EVIDENCE,)
            ),
            RetentionAction.APPEND_ONLY_AUDIT if table in _APPEND_ONLY else RetentionAction.PRESERVE,
            exportable=True,
            rationale="Authoritative commercial/operational evidence retained for historical reproducibility.",
        )
        for table in _EVIDENCE_EXPORT
    ),
    _asset(
        "tender_admin_corrections",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.PERSONAL_DATA, DataClass.SECURITY_AUDIT),
        RetentionAction.APPEND_ONLY_AUDIT,
        exportable=True,
        rationale="Privileged correction history with free-form reason evidence.",
    ),
    _asset(
        "fx_rates",
        (DataClass.IMMUTABLE_EVIDENCE,),
        RetentionAction.APPEND_ONLY_AUDIT,
        rationale="Bitemporal FX observations required for historical reproducibility.",
    ),
    _asset(
        "outbox_events",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.PERSONAL_DATA, DataClass.SECURITY_AUDIT),
        RetentionAction.APPEND_ONLY_AUDIT,
        rationale="Canonical event history plus delivery evidence.",
    ),
    _asset(
        "outbox_consumer_receipts",
        (DataClass.SECURITY_AUDIT,),
        RetentionAction.PRESERVE,
        rationale="Durable consumer-deduplication evidence.",
    ),
    _asset(
        "evidence_integrity_entries",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.SECURITY_AUDIT),
        RetentionAction.APPEND_ONLY_AUDIT,
        rationale="Tamper-evidence ledger; deleting it destroys integrity verification.",
    ),
    _asset(
        "evidence_integrity_checkpoints",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.SECURITY_AUDIT),
        RetentionAction.APPEND_ONLY_AUDIT,
        rationale="Integrity roots anchored to evidence streams.",
    ),
    _asset(
        "idempotency_records",
        (DataClass.OPERATIONAL_TELEMETRY, DataClass.PERSONAL_DATA, DataClass.SECURITY_AUDIT),
        RetentionAction.POLICY_TTL_DELETE,
        hold=False,
        delete=True,
        rationale="Bounded non-authoritative replay cache governed by explicit TTL.",
    ),
    _asset(
        "api_rate_limit_windows",
        (DataClass.OPERATIONAL_TELEMETRY, DataClass.SECURITY_AUDIT),
        RetentionAction.POLICY_TTL_DELETE,
        hold=False,
        delete=True,
        rationale="Transient hashed abuse-control counters with no business authority.",
    ),
    *tuple(
        _asset(
            table,
            (
                (DataClass.DERIVED_REBUILDABLE, DataClass.PERSONAL_DATA)
                if table in _PERSONAL_CAPABLE
                else (DataClass.DERIVED_REBUILDABLE,)
            ),
            RetentionAction.REBUILDABLE_PURGE,
            hold=False,
            rationale="Derived Charter Graph state; PostgreSQL events remain canonical.",
        )
        for table in (
            "charter_graph_projection_versions",
            "charter_graph_projection_checkpoints",
            "charter_graph_aggregate_cursors",
            "charter_graph_nodes",
            "charter_graph_edges",
        )
    ),
    _asset(
        "data_governance_legal_holds",
        (DataClass.SECURITY_AUDIT, DataClass.LEGAL_HOLD, DataClass.PERSONAL_DATA),
        RetentionAction.PRESERVE,
        hold=False,
        rationale="Control-plane hold state; reasons are restricted governance material.",
    ),
    _asset(
        "data_governance_lifecycle_operations",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.SECURITY_AUDIT, DataClass.LEGAL_HOLD),
        RetentionAction.APPEND_ONLY_AUDIT,
        hold=False,
        rationale="Immutable closure/erasure decision and dependency report.",
    ),
    _asset(
        "data_governance_events",
        (DataClass.IMMUTABLE_EVIDENCE, DataClass.SECURITY_AUDIT, DataClass.LEGAL_HOLD),
        RetentionAction.APPEND_ONLY_AUDIT,
        hold=False,
        rationale="Append-only governance event history protected by evidence integrity.",
    ),
)
DATA_ASSET_POLICY_BY_TABLE = {item.table: item for item in DATA_ASSET_POLICIES}

EXTERNAL_DATA_ASSET_POLICIES: tuple[ExternalDataAssetPolicy, ...] = (
    ExternalDataAssetPolicy(
        "application_logs",
        frozenset({DataClass.OPERATIONAL_TELEMETRY, DataClass.PERSONAL_DATA}),
        RetentionAction.CLOSE_OR_REVIEW,
        False,
        True,
        "Deployment-owned logs need a platform retention policy and are not PostgreSQL lifecycle data.",
    ),
    ExternalDataAssetPolicy(
        "authentication_security_logs",
        frozenset({DataClass.SECURITY_AUDIT, DataClass.OPERATIONAL_TELEMETRY, DataClass.PERSONAL_DATA}),
        RetentionAction.CLOSE_OR_REVIEW,
        False,
        True,
        "Identity/security telemetry is restricted and excluded from tenant exports.",
    ),
)

TENANT_DEPENDENCY_GRAPH: tuple[TenantDependency, ...] = tuple(
    TenantDependency(kind, table, relation, blocker, rationale)
    for kind, table, relation, blocker, rationale in (
        (TenantKind.BUYER, "missions", "missions.buyer_id", True, "buyer intent"),
        (TenantKind.BUYER, "procurement_approvals", "buyer_id", True, "award authority"),
        (TenantKind.BUYER, "contracts", "buyer_id", True, "contract evidence"),
        (TenantKind.BUYER, "fx_locks", "buyer_id", True, "FX commitment"),
        (TenantKind.BUYER, "financial_reconciliations", "buyer_id", True, "financial evidence"),
        (TenantKind.BUYER, "reconciliation_disputes", "buyer_id", True, "dispute evidence"),
        (TenantKind.BUYER, "reconciliation_variance_approvals", "buyer_id", True, "approval evidence"),
        (TenantKind.BUYER, "outbox_events", "organization aggregate", True, "canonical history"),
        (TenantKind.OPERATOR, "aircraft", "aircraft.operator_id", False, "fleet master"),
        (TenantKind.OPERATOR, "rfqs", "rfqs.operator_id", True, "procurement evidence"),
        (TenantKind.OPERATOR, "bookings", "bookings.operator_id", True, "award evidence"),
        (TenantKind.OPERATOR, "aircraft_capacity_reservations", "operator_id", True, "capacity authority"),
        (TenantKind.OPERATOR, "contracts", "operator_id", True, "contract evidence"),
        (TenantKind.OPERATOR, "disruption_proposals", "operator_id", True, "disruption evidence"),
        (TenantKind.OPERATOR, "financial_reconciliations", "operator_id", True, "financial evidence"),
        (TenantKind.OPERATOR, "tender_invitations", "operator_id", True, "tender evidence"),
        (TenantKind.OPERATOR, "aircraft_position_observations", "fleet history", True, "no-hindsight evidence"),
        (TenantKind.OPERATOR, "aircraft_availability_records", "fleet history", True, "availability evidence"),
        (TenantKind.OPERATOR, "quotes", "operator aircraft quotes", True, "commercial evidence"),
        (TenantKind.OPERATOR, "outbox_events", "org/operator/aircraft aggregates", True, "canonical history"),
    )
)


class DataGovernanceRepository(Protocol):
    def lock_tenant(self, tenant_kind: TenantKind, tenant_id: UUID) -> None: ...
    def tenant_exists(self, tenant_kind: TenantKind, tenant_id: UUID) -> bool: ...
    def find_lifecycle_operation(self, *, tenant_kind: TenantKind, tenant_id: UUID, operation: LifecycleOperation, request_key_digest: str) -> LifecycleOutcome | None: ...
    def close_tenant(self, *, tenant_kind: TenantKind, tenant_id: UUID, recorded_at: datetime, correlation_id: CorrelationId) -> list[str]: ...
    def active_legal_hold(self, tenant_kind: TenantKind, tenant_id: UUID) -> LegalHold | None: ...
    def erasure_dependency_counts(self, tenant_kind: TenantKind, tenant_id: UUID) -> dict[str, int]: ...
    def erase_tenant(self, tenant_kind: TenantKind, tenant_id: UUID) -> list[str]: ...
    def add_lifecycle_operation(self, *, tenant_kind: TenantKind, tenant_id: UUID, operation: LifecycleOperation, status: LifecycleStatus, request_key_digest: str, request_hash: str, actor_subject_digest: str, requested_at: datetime, report: dict[str, object]) -> LifecycleOutcome: ...
    def append_governance_event(self, *, tenant_kind: TenantKind, tenant_id: UUID, event_type: str, related_id: UUID | None, actor_subject_digest: str, recorded_at: datetime, details: dict[str, object]) -> None: ...
    def create_legal_hold(self, *, tenant_kind: TenantKind, tenant_id: UUID, reason: str, actor_subject_digest: str, recorded_at: datetime) -> LegalHold: ...
    def get_legal_hold_for_update(self, hold_id: UUID) -> LegalHold | None: ...
    def release_legal_hold(self, *, hold_id: UUID, reason: str, actor_subject_digest: str, recorded_at: datetime) -> LegalHold: ...
    def list_legal_holds(self, tenant_kind: TenantKind, tenant_id: UUID) -> list[LegalHold]: ...


class DataGovernanceService:
    def __init__(self, repository: DataGovernanceRepository) -> None:
        self._repository = repository

    def close_tenant(self, *, tenant_kind: TenantKind, tenant_id: UUID, request_key_digest: str, request_hash: str, actor_subject_digest: str, recorded_at: datetime, correlation_id: CorrelationId) -> LifecycleOutcome:
        self._repository.lock_tenant(tenant_kind, tenant_id)
        replay = self._replay(tenant_kind, tenant_id, LifecycleOperation.CLOSURE, request_key_digest, request_hash)
        if replay is not None:
            return replay
        self._require_tenant(tenant_kind, tenant_id)
        hold = self._repository.active_legal_hold(tenant_kind, tenant_id)
        mutations = self._repository.close_tenant(tenant_kind=tenant_kind, tenant_id=tenant_id, recorded_at=recorded_at, correlation_id=correlation_id)
        report: dict[str, object] = {
            "policy_version": DATA_GOVERNANCE_POLICY_VERSION,
            "operation": LifecycleOperation.CLOSURE.value,
            "tenant_kind": tenant_kind.value,
            "tenant_id": str(tenant_id),
            "legal_hold_active": hold is not None,
            "mutations": mutations,
            "records_deleted": 0,
            "immutable_evidence_preserved": True,
            "note": "Closure changes current master status; it does not erase historical evidence.",
        }
        outcome = self._repository.add_lifecycle_operation(tenant_kind=tenant_kind, tenant_id=tenant_id, operation=LifecycleOperation.CLOSURE, status=LifecycleStatus.COMPLETED, request_key_digest=request_key_digest, request_hash=request_hash, actor_subject_digest=actor_subject_digest, requested_at=recorded_at, report=report)
        self._repository.append_governance_event(tenant_kind=tenant_kind, tenant_id=tenant_id, event_type="TENANT_CLOSED", related_id=outcome.id, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at, details=report)
        return outcome

    def erase_tenant(self, *, tenant_kind: TenantKind, tenant_id: UUID, request_key_digest: str, request_hash: str, actor_subject_digest: str, recorded_at: datetime) -> LifecycleOutcome:
        self._repository.lock_tenant(tenant_kind, tenant_id)
        replay = self._replay(tenant_kind, tenant_id, LifecycleOperation.ERASURE, request_key_digest, request_hash)
        if replay is not None:
            return replay
        self._require_tenant(tenant_kind, tenant_id)
        hold = self._repository.active_legal_hold(tenant_kind, tenant_id)
        dependencies = self._repository.erasure_dependency_counts(tenant_kind, tenant_id)
        blockers = {name: count for name, count in dependencies.items() if count > 0}
        if hold is not None:
            blockers["active_legal_hold"] = 1
        if blockers:
            status = LifecycleStatus.BLOCKED
            deleted: list[str] = []
            note = "Erasure refused; no partial deletion was performed."
        else:
            status = LifecycleStatus.COMPLETED
            deleted = self._repository.erase_tenant(tenant_kind, tenant_id)
            note = "Only dependency-free master/derived state was deleted atomically."
        report: dict[str, object] = {
            "policy_version": DATA_GOVERNANCE_POLICY_VERSION,
            "operation": LifecycleOperation.ERASURE.value,
            "tenant_kind": tenant_kind.value,
            "tenant_id": str(tenant_id),
            "status": status.value,
            "dependency_counts": dependencies,
            "blockers": blockers,
            "deleted": deleted,
            "immutable_evidence_preserved": True,
            "note": note,
        }
        outcome = self._repository.add_lifecycle_operation(tenant_kind=tenant_kind, tenant_id=tenant_id, operation=LifecycleOperation.ERASURE, status=status, request_key_digest=request_key_digest, request_hash=request_hash, actor_subject_digest=actor_subject_digest, requested_at=recorded_at, report=report)
        event_type = "TENANT_ERASURE_COMPLETED" if status is LifecycleStatus.COMPLETED else "TENANT_ERASURE_BLOCKED"
        self._repository.append_governance_event(tenant_kind=tenant_kind, tenant_id=tenant_id, event_type=event_type, related_id=outcome.id, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at, details=report)
        return outcome

    def create_legal_hold(self, *, tenant_kind: TenantKind, tenant_id: UUID, reason: str, actor_subject_digest: str, recorded_at: datetime) -> LegalHold:
        self._repository.lock_tenant(tenant_kind, tenant_id)
        self._require_tenant(tenant_kind, tenant_id)
        existing = self._repository.active_legal_hold(tenant_kind, tenant_id)
        if existing is not None:
            if existing.reason == reason:
                return existing
            raise EntityConflictError("tenant already has an active legal hold")
        hold = self._repository.create_legal_hold(tenant_kind=tenant_kind, tenant_id=tenant_id, reason=reason, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at)
        self._repository.append_governance_event(tenant_kind=tenant_kind, tenant_id=tenant_id, event_type="LEGAL_HOLD_CREATED", related_id=hold.id, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at, details={"hold_id": str(hold.id), "reason": reason})
        return hold

    def release_legal_hold(self, *, hold_id: UUID, reason: str, actor_subject_digest: str, recorded_at: datetime) -> LegalHold:
        hold = self._repository.get_legal_hold_for_update(hold_id)
        if hold is None:
            raise EntityNotFoundError("legal hold does not exist")
        self._repository.lock_tenant(hold.tenant_kind, hold.tenant_id)
        if hold.status == "released":
            return hold
        released = self._repository.release_legal_hold(hold_id=hold_id, reason=reason, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at)
        self._repository.append_governance_event(tenant_kind=released.tenant_kind, tenant_id=released.tenant_id, event_type="LEGAL_HOLD_RELEASED", related_id=released.id, actor_subject_digest=actor_subject_digest, recorded_at=recorded_at, details={"hold_id": str(released.id), "release_reason": reason})
        return released

    def _require_tenant(self, tenant_kind: TenantKind, tenant_id: UUID) -> None:
        if not self._repository.tenant_exists(tenant_kind, tenant_id):
            raise EntityNotFoundError("tenant does not exist")

    def _replay(self, tenant_kind: TenantKind, tenant_id: UUID, operation: LifecycleOperation, request_key_digest: str, request_hash: str) -> LifecycleOutcome | None:
        existing = self._repository.find_lifecycle_operation(tenant_kind=tenant_kind, tenant_id=tenant_id, operation=operation, request_key_digest=request_key_digest)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise EntityConflictError("governance idempotency key was already used with a different request")
        return existing


def policy_for_table(table: str) -> DataAssetPolicy:
    try:
        return DATA_ASSET_POLICY_BY_TABLE[table]
    except KeyError as exc:
        raise EntityConflictError(f"unclassified data asset: {table}") from exc


def assert_automated_deletion_allowed(table: str) -> None:
    if not policy_for_table(table).automated_delete_allowed:
        raise EntityConflictError(f"automated deletion is not permitted for {table}")
