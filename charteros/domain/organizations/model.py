from __future__ import annotations

from enum import StrEnum

from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class OrganizationId(TypedId):
    __slots__ = ()


class OrganizationType(StrEnum):
    BUYER = "buyer"
    OPERATOR = "operator"
    BROKER = "broker"
    TMC = "tmc"
    DMC = "dmc"
    TOUR_OPERATOR = "tour_operator"
    CORPORATE = "corporate"


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


def normalize_name(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not 2 <= len(normalized) <= 200:
        raise DomainValidationError(f"{field_name} must contain 2 to 200 characters")
    return normalized


def normalize_country(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
        raise DomainValidationError("country must be a two-letter uppercase ISO-style code")
    return normalized


class Organization(AggregateRoot[OrganizationId]):
    aggregate_type = "organization"

    def __init__(
        self,
        organization_id: OrganizationId,
        *,
        organization_type: OrganizationType,
        legal_name: str,
        trading_name: str | None,
        country: str,
        status: OrganizationStatus,
        version: int = 0,
    ) -> None:
        super().__init__(organization_id, version=version)
        self.organization_type = OrganizationType(organization_type)
        self.legal_name = normalize_name(legal_name, field_name="legal_name")
        self.trading_name = (
            normalize_name(trading_name, field_name="trading_name") if trading_name else None
        )
        self.country = normalize_country(country)
        self.status = OrganizationStatus(status)

    @classmethod
    def create(
        cls,
        *,
        organization_type: OrganizationType,
        legal_name: str,
        trading_name: str | None,
        country: str,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
        correlation_id: CorrelationId | None = None,
    ) -> Organization:
        organization = cls(
            OrganizationId.new(),
            organization_type=organization_type,
            legal_name=legal_name,
            trading_name=trading_name,
            country=country,
            status=status,
        )
        organization._record_event(
            "ORGANIZATION_CREATED",
            {
                "organization_type": organization.organization_type.value,
                "legal_name": organization.legal_name,
                "trading_name": organization.trading_name,
                "country": organization.country,
                "status": organization.status.value,
            },
            correlation_id=correlation_id,
        )
        return organization
