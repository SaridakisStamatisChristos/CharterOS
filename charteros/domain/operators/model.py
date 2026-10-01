from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class OperatorId(TypedId):
    __slots__ = ()


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class InsuranceStatus(StrEnum):
    UNKNOWN = "unknown"
    VALID = "valid"
    EXPIRED = "expired"
    REJECTED = "rejected"


class CommercialStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


def _canonical_token(value: str, *, field_name: str, max_length: int = 80) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= max_length:
        raise DomainValidationError(f"{field_name} must contain 1 to {max_length} characters")
    return normalized.upper()


def _canonical_list(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(_canonical_token(v, field_name=field_name) for v in values))
    if not normalized:
        raise DomainValidationError(f"{field_name} must contain at least one value")
    return normalized


class Operator(AggregateRoot[OperatorId]):
    aggregate_type = "operator"

    def __init__(
        self,
        operator_id: OperatorId,
        *,
        organization_id: OrganizationId,
        aoc_reference: str,
        operating_regions: tuple[str, ...],
        verification_status: VerificationStatus,
        insurance_status: InsuranceStatus,
        safety_documents: tuple[str, ...],
        commercial_status: CommercialStatus,
        version: int = 0,
    ) -> None:
        super().__init__(operator_id, version=version)
        self.organization_id = organization_id
        self.aoc_reference = _canonical_token(aoc_reference, field_name="aoc_reference")
        self.operating_regions = _canonical_list(operating_regions, field_name="operating_regions")
        self.verification_status = VerificationStatus(verification_status)
        self.insurance_status = InsuranceStatus(insurance_status)
        self.safety_documents = tuple(
            dict.fromkeys(
                " ".join(document.split())
                for document in safety_documents
                if " ".join(document.split())
            )
        )
        self.commercial_status = CommercialStatus(commercial_status)

    @classmethod
    def create(
        cls,
        *,
        organization_id: OrganizationId,
        aoc_reference: str,
        operating_regions: tuple[str, ...],
        recorded_at: datetime,
        verification_status: VerificationStatus = VerificationStatus.PENDING,
        insurance_status: InsuranceStatus = InsuranceStatus.UNKNOWN,
        safety_documents: tuple[str, ...] = (),
        commercial_status: CommercialStatus = CommercialStatus.ACTIVE,
        correlation_id: CorrelationId | None = None,
    ) -> Operator:
        operator = cls(
            OperatorId.new(),
            organization_id=organization_id,
            aoc_reference=aoc_reference,
            operating_regions=operating_regions,
            verification_status=verification_status,
            insurance_status=insurance_status,
            safety_documents=safety_documents,
            commercial_status=commercial_status,
        )
        operator._record_event(
            "OPERATOR_REGISTERED",
            {
                "organization_id": str(operator.organization_id),
                "aoc_reference": operator.aoc_reference,
                "operating_regions": list(operator.operating_regions),
                "verification_status": operator.verification_status.value,
                "insurance_status": operator.insurance_status.value,
                "commercial_status": operator.commercial_status.value,
            },
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        return operator

    def close_for_governance(
        self,
        *,
        recorded_at: datetime,
        correlation_id: CorrelationId,
        policy_version: str,
    ) -> bool:
        if self.commercial_status is CommercialStatus.INACTIVE:
            return False
        previous_status = self.commercial_status
        self.commercial_status = CommercialStatus.INACTIVE
        self._record_event(
            "OPERATOR_CLOSED",
            {
                "previous_commercial_status": previous_status.value,
                "commercial_status": self.commercial_status.value,
                "policy_version": policy_version,
            },
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        return True
