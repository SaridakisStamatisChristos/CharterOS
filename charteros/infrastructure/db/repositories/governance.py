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
