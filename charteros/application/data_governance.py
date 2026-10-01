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
