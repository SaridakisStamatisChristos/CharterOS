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
