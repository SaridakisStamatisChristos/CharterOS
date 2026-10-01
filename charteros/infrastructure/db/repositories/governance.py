from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from charteros.application.data_governance import (
    DATA_GOVERNANCE_POLICY_VERSION,
    LegalHold,
    LifecycleOperation,
    LifecycleOutcome,
    LifecycleStatus,
    TenantKind,
)
from charteros.application.exceptions import EntityNotFoundError
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.models.catalog import AircraftRow, OperatorRow, OrganizationRow
from charteros.infrastructure.db.models.governance import (
    DataGovernanceEventRow,
    DataGovernanceLegalHoldRow,
    DataGovernanceLifecycleOperationRow,
)
from charteros.infrastructure.db.repositories.catalog import (
    SqlAlchemyDomainEventRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
)


class SqlAlchemyDataGovernanceRepository:
    """Explicit tenant lifecycle authority over canonical PostgreSQL state.

    Every destructive path is dependency-aware. No FK is relaxed and no broad cascading delete is used.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_tenant(self, tenant_kind: TenantKind, tenant_id: UUID) -> None:
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"data-governance:{tenant_kind.value}:{tenant_id}"},
            )

    def tenant_exists(self, tenant_kind: TenantKind, tenant_id: UUID) -> bool:
        if tenant_kind is TenantKind.BUYER:
            return self._session.get(OrganizationRow, tenant_id) is not None
        return self._session.get(OperatorRow, tenant_id) is not None

    def find_lifecycle_operation(
        self,
        *,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        operation: LifecycleOperation,
        request_key_digest: str,
    ) -> LifecycleOutcome | None:
        row = self._session.scalar(
            select(DataGovernanceLifecycleOperationRow).where(
                DataGovernanceLifecycleOperationRow.tenant_kind == tenant_kind.value,
                DataGovernanceLifecycleOperationRow.tenant_id == tenant_id,
                DataGovernanceLifecycleOperationRow.operation == operation.value,
                DataGovernanceLifecycleOperationRow.request_key_digest == request_key_digest,
            )
        )
        return self._lifecycle(row) if row is not None else None

    def close_tenant(
        self,
        *,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        recorded_at: datetime,
        correlation_id: CorrelationId,
    ) -> list[str]:
        organizations = SqlAlchemyOrganizationRepository(self._session)
        operators = SqlAlchemyOperatorRepository(self._session)
        events = SqlAlchemyDomainEventRepository(self._session)
        mutations: list[str] = []

        if tenant_kind is TenantKind.BUYER:
            organization = organizations.get_for_update(OrganizationId(tenant_id))
            if organization is None:
                raise EntityNotFoundError("tenant does not exist")
            expected_version = organization.version
            if organization.close_for_governance(
                recorded_at=recorded_at,
                correlation_id=correlation_id,
                policy_version=DATA_GOVERNANCE_POLICY_VERSION,
            ):
                organizations.save(organization, expected_version=expected_version)
                events.add_aggregate_events(organization)
                mutations.append("organizations.status=inactive")
            return mutations

        operator = operators.get_for_update(OperatorId(tenant_id))
        if operator is None:
            raise EntityNotFoundError("tenant does not exist")
        organization = organizations.get_for_update(operator.organization_id)
        if organization is None:
            raise EntityNotFoundError("operator organization does not exist")

        operator_expected = operator.version
        if operator.close_for_governance(
            recorded_at=recorded_at,
            correlation_id=correlation_id,
            policy_version=DATA_GOVERNANCE_POLICY_VERSION,
        ):
            operators.save(operator, expected_version=operator_expected)
            events.add_aggregate_events(operator)
            mutations.append("operators.commercial_status=inactive")

        organization_expected = organization.version
        if organization.close_for_governance(
            recorded_at=recorded_at,
            correlation_id=correlation_id,
            policy_version=DATA_GOVERNANCE_POLICY_VERSION,
        ):
            organizations.save(organization, expected_version=organization_expected)
            events.add_aggregate_events(organization)
            mutations.append("organizations.status=inactive")
        return mutations

    def active_legal_hold(self, tenant_kind: TenantKind, tenant_id: UUID) -> LegalHold | None:
        row = self._session.scalar(
            select(DataGovernanceLegalHoldRow).where(
                DataGovernanceLegalHoldRow.tenant_kind == tenant_kind.value,
                DataGovernanceLegalHoldRow.tenant_id == tenant_id,
                DataGovernanceLegalHoldRow.status == "active",
            )
        )
        return self._legal_hold(row) if row is not None else None

    def erasure_dependency_counts(
        self, tenant_kind: TenantKind, tenant_id: UUID
    ) -> dict[str, int]:
        if tenant_kind is TenantKind.BUYER:
            return self._buyer_dependency_counts(tenant_id)
        return self._operator_dependency_counts(tenant_id)

    def erase_tenant(self, tenant_kind: TenantKind, tenant_id: UUID) -> list[str]:
        deleted: list[str] = []
        if tenant_kind is TenantKind.BUYER:
            self._purge_derived_for_ids((tenant_id,))
            organization = self._session.get(OrganizationRow, tenant_id)
            if organization is None:
                raise EntityNotFoundError("tenant does not exist")
            self._session.delete(organization)
            self._session.flush()
            deleted.append("organizations")
            return deleted

        operator = self._session.get(OperatorRow, tenant_id)
        if operator is None:
            raise EntityNotFoundError("tenant does not exist")
        organization_id = operator.organization_id
        aircraft_ids = tuple(
            self._session.scalars(
                select(AircraftRow.id).where(AircraftRow.operator_id == tenant_id)
            ).all()
        )
        self._purge_derived_for_ids((organization_id, tenant_id, *aircraft_ids))

        if aircraft_ids:
            self._session.execute(delete(AircraftRow).where(AircraftRow.operator_id == tenant_id))
            deleted.append("aircraft")
        self._session.delete(operator)
        deleted.append("operators")
        organization = self._session.get(OrganizationRow, organization_id)
        if organization is not None:
            self._session.delete(organization)
            deleted.append("organizations")
        self._session.flush()
        return deleted

    def add_lifecycle_operation(
        self,
        *,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        operation: LifecycleOperation,
        status: LifecycleStatus,
        request_key_digest: str,
        request_hash: str,
        actor_subject_digest: str,
        requested_at: datetime,
        report: dict[str, object],
    ) -> LifecycleOutcome:
        row = DataGovernanceLifecycleOperationRow(
            id=uuid4(),
            tenant_kind=tenant_kind.value,
            tenant_id=tenant_id,
            operation=operation.value,
            status=status.value,
            request_key_digest=request_key_digest,
            request_hash=request_hash,
            actor_subject_digest=actor_subject_digest,
            policy_version=DATA_GOVERNANCE_POLICY_VERSION,
            report=report,
            requested_at=requested_at,
            completed_at=requested_at,
        )
        self._session.add(row)
        self._session.flush()
        return self._lifecycle(row)

    def append_governance_event(
        self,
        *,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        event_type: str,
        related_id: UUID | None,
        actor_subject_digest: str,
        recorded_at: datetime,
        details: dict[str, object],
    ) -> None:
        self._session.add(
            DataGovernanceEventRow(
                id=uuid4(),
                tenant_kind=tenant_kind.value,
                tenant_id=tenant_id,
                event_type=event_type,
                related_id=related_id,
                actor_subject_digest=actor_subject_digest,
                policy_version=DATA_GOVERNANCE_POLICY_VERSION,
                details=details,
                recorded_at=recorded_at,
            )
        )
        self._session.flush()

    def create_legal_hold(
        self,
        *,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        reason: str,
        actor_subject_digest: str,
        recorded_at: datetime,
    ) -> LegalHold:
        row = DataGovernanceLegalHoldRow(
            id=uuid4(),
            tenant_kind=tenant_kind.value,
            tenant_id=tenant_id,
            reason=reason,
            status="active",
            created_at=recorded_at,
            created_by_digest=actor_subject_digest,
            released_at=None,
            released_by_digest=None,
            release_reason=None,
        )
        self._session.add(row)
        self._session.flush()
        return self._legal_hold(row)

    def get_legal_hold_for_update(self, hold_id: UUID) -> LegalHold | None:
        row = self._session.scalar(
            select(DataGovernanceLegalHoldRow)
            .where(DataGovernanceLegalHoldRow.id == hold_id)
            .with_for_update()
        )
        return self._legal_hold(row) if row is not None else None

    def release_legal_hold(
        self,
        *,
        hold_id: UUID,
        reason: str,
        actor_subject_digest: str,
        recorded_at: datetime,
    ) -> LegalHold:
        row = self._session.get(DataGovernanceLegalHoldRow, hold_id)
        if row is None:
            raise EntityNotFoundError("legal hold does not exist")
        row.status = "released"
        row.released_at = recorded_at
        row.released_by_digest = actor_subject_digest
        row.release_reason = reason
        self._session.flush()
        return self._legal_hold(row)

    def list_legal_holds(self, tenant_kind: TenantKind, tenant_id: UUID) -> list[LegalHold]:
        rows = self._session.scalars(
            select(DataGovernanceLegalHoldRow)
            .where(
                DataGovernanceLegalHoldRow.tenant_kind == tenant_kind.value,
                DataGovernanceLegalHoldRow.tenant_id == tenant_id,
            )
            .order_by(DataGovernanceLegalHoldRow.created_at, DataGovernanceLegalHoldRow.id)
        ).all()
        return [self._legal_hold(row) for row in rows]

    def export_master_data(self, tenant_kind: TenantKind, tenant_id: UUID) -> dict[str, object]:
        if tenant_kind is TenantKind.BUYER:
            organization = self._session.get(OrganizationRow, tenant_id)
            if organization is None:
                raise EntityNotFoundError("tenant does not exist")
            return {"organization": self._organization_record(organization)}

        operator = self._session.get(OperatorRow, tenant_id)
        if operator is None:
            raise EntityNotFoundError("tenant does not exist")
        organization = self._session.get(OrganizationRow, operator.organization_id)
        if organization is None:
            raise EntityNotFoundError("operator organization does not exist")
        aircraft = self._session.scalars(
            select(AircraftRow)
            .where(AircraftRow.operator_id == tenant_id)
            .order_by(AircraftRow.id)
        ).all()
        return {
            "organization": self._organization_record(organization),
            "operator": {
                "id": str(operator.id),
                "organization_id": str(operator.organization_id),
                "aoc_reference": operator.aoc_reference,
                "operating_regions": list(operator.operating_regions),
                "verification_status": operator.verification_status,
                "insurance_status": operator.insurance_status,
                "safety_documents": list(operator.safety_documents),
                "commercial_status": operator.commercial_status,
                "created_at": operator.created_at.isoformat(),
            },
            "aircraft": [self._aircraft_record(row) for row in aircraft],
        }

    def mission_ids_for_tenant(
        self,
        tenant_kind: TenantKind,
        tenant_id: UUID,
        *,
        limit: int,
    ) -> list[UUID]:
        if limit < 1:
            raise ValueError("mission export limit must be positive")
        if tenant_kind is TenantKind.BUYER:
            rows = self._session.execute(
                text(
                    "SELECT id FROM missions WHERE buyer_id = :tenant_id "
                    "ORDER BY id LIMIT :limit"
                ),
                {"tenant_id": tenant_id, "limit": limit},
            ).scalars()
            return list(rows)

        rows = self._session.execute(
            text(
                "SELECT mission_id FROM ("
                "SELECT mission_id FROM rfqs WHERE operator_id = :tenant_id "
                "UNION SELECT mission_id FROM bookings WHERE operator_id = :tenant_id"
                ") AS tenant_missions ORDER BY mission_id LIMIT :limit"
            ),
            {"tenant_id": tenant_id, "limit": limit},
        ).scalars()
        return list(rows)

    def _buyer_dependency_counts(self, tenant_id: UUID) -> dict[str, int]:
        queries = {
            "operators": "SELECT count(*) FROM operators WHERE organization_id = :tenant_id",
            "missions": "SELECT count(*) FROM missions WHERE buyer_id = :tenant_id",
            "procurement_approvals": (
                "SELECT count(*) FROM procurement_approvals WHERE buyer_id = :tenant_id"
            ),
            "contracts": "SELECT count(*) FROM contracts WHERE buyer_id = :tenant_id",
            "fx_locks": "SELECT count(*) FROM fx_locks WHERE buyer_id = :tenant_id",
            "financial_reconciliations": (
                "SELECT count(*) FROM financial_reconciliations WHERE buyer_id = :tenant_id"
            ),
            "reconciliation_disputes": (
                "SELECT count(*) FROM reconciliation_disputes WHERE buyer_id = :tenant_id"
