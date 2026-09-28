from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from charteros.domain.bookings import BookingId
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class ContractId(TypedId):
    __slots__ = ()


class ContractStatus(StrEnum):
    PENDING_ACCEPTANCE = "pending_acceptance"
    PARTIALLY_ACCEPTED = "partially_accepted"
    ACCEPTED = "accepted"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_document_reference(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError("document_reference is required")
    if len(normalized) > 1000:
        raise DomainValidationError("document_reference cannot exceed 1000 characters")
    return normalized


def _canonical_metadata(values: Mapping[str, str]) -> Mapping[str, str]:
    if len(values) > 64:
        raise DomainValidationError("contract metadata cannot contain more than 64 entries")
    normalized: dict[str, str] = {}
    seen: set[str] = set()
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise DomainValidationError("contract metadata keys and values must be strings")
        key = " ".join(raw_key.split())
        value = " ".join(raw_value.split())
        if not key:
            raise DomainValidationError("contract metadata keys cannot be blank")
        if len(key) > 100:
            raise DomainValidationError("contract metadata keys cannot exceed 100 characters")
        if len(value) > 500:
            raise DomainValidationError("contract metadata values cannot exceed 500 characters")
        folded = key.casefold()
        if folded in seen:
            raise DomainValidationError("contract metadata keys must be unique case-insensitively")
        seen.add(folded)
        normalized[key] = value
    return MappingProxyType(dict(sorted(normalized.items(), key=lambda item: item[0].casefold())))


class Contract(AggregateRoot[ContractId]):
    aggregate_type = "contract"

    def __init__(
        self,
        contract_id: ContractId,
        *,
        booking_id: BookingId,
        buyer_id: OrganizationId,
        operator_id: OperatorId,
        document_reference: str,
        document_version: int,
        metadata: Mapping[str, str],
        status: ContractStatus,
        created_at: datetime,
        buyer_signed_at: datetime | None = None,
        operator_signed_at: datetime | None = None,
        accepted_at: datetime | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(contract_id, version=version)
        if (
            not isinstance(document_version, int)
            or isinstance(document_version, bool)
            or document_version < 1
        ):
            raise DomainValidationError("document_version must be a positive integer")

        self.booking_id = booking_id
        self.buyer_id = buyer_id
        self.operator_id = operator_id
        self.document_reference = _canonical_document_reference(document_reference)
        self.document_version = document_version
        self.metadata = _canonical_metadata(metadata)
        self.status = ContractStatus(status)
        self.created_at = _utc(created_at, field_name="created_at")
        self.buyer_signed_at = (
            _utc(buyer_signed_at, field_name="buyer_signed_at")
            if buyer_signed_at is not None
            else None
        )
        self.operator_signed_at = (
            _utc(operator_signed_at, field_name="operator_signed_at")
            if operator_signed_at is not None
            else None
        )
        self.accepted_at = (
            _utc(accepted_at, field_name="accepted_at") if accepted_at is not None else None
        )
        self._validate_state()

    @classmethod
    def create(
        cls,
        *,
        booking_id: BookingId,
        buyer_id: OrganizationId,
        operator_id: OperatorId,
        document_reference: str,
        document_version: int,
        metadata: Mapping[str, str],
        created_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Contract:
        contract = cls(
            ContractId.new(),
            booking_id=booking_id,
            buyer_id=buyer_id,
            operator_id=operator_id,
            document_reference=document_reference,
            document_version=document_version,
            metadata=metadata,
            status=ContractStatus.PENDING_ACCEPTANCE,
            created_at=created_at,
        )
        contract._record_event(
            "CONTRACT_CREATED",
            {
                "booking_id": str(contract.booking_id),
                "buyer_id": str(contract.buyer_id),
                "operator_id": str(contract.operator_id),
                "document_reference": contract.document_reference,
                "document_version": contract.document_version,
                "metadata": dict(contract.metadata),
                "status": contract.status.value,
            },
            correlation_id=correlation_id,
            occurred_at=contract.created_at,
        )
        return contract

    def accept_buyer(
        self,
        *,
        signed_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.buyer_signed_at is not None:
            raise DomainValidationError("buyer has already accepted this contract")
        when = self._validated_signature_time(signed_at, field_name="buyer_signed_at")
        self.buyer_signed_at = when
        self._record_event(
            "CONTRACT_BUYER_ACCEPTED",
            {
                "buyer_id": str(self.buyer_id),
                "signed_at": self._iso(when),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )
        self._advance_acceptance(when=when, correlation_id=correlation_id)

    def accept_operator(
        self,
        *,
        signed_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.operator_signed_at is not None:
            raise DomainValidationError("operator has already accepted this contract")
        when = self._validated_signature_time(signed_at, field_name="operator_signed_at")
        self.operator_signed_at = when
        self._record_event(
            "CONTRACT_OPERATOR_ACCEPTED",
            {
                "operator_id": str(self.operator_id),
                "signed_at": self._iso(when),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )
        self._advance_acceptance(when=when, correlation_id=correlation_id)

    def _advance_acceptance(
        self,
        *,
        when: datetime,
        correlation_id: CorrelationId | None,
    ) -> None:
        if self.buyer_signed_at is not None and self.operator_signed_at is not None:
            self.status = ContractStatus.ACCEPTED
            self.accepted_at = max(self.buyer_signed_at, self.operator_signed_at)
            self._record_event(
                "CONTRACT_ACCEPTED",
                {
                    "booking_id": str(self.booking_id),
                    "buyer_signed_at": self._iso(self.buyer_signed_at),
                    "operator_signed_at": self._iso(self.operator_signed_at),
                    "accepted_at": self._iso(self.accepted_at),
                },
                correlation_id=correlation_id,
                occurred_at=when,
            )
        else:
            self.status = ContractStatus.PARTIALLY_ACCEPTED

    def _validated_signature_time(self, value: datetime, *, field_name: str) -> datetime:
        when = _utc(value, field_name=field_name)
        if when < self.created_at:
            raise DomainValidationError(f"{field_name} cannot precede contract creation")
        return when

    def _validate_state(self) -> None:
        for field_name, value in (
            ("buyer_signed_at", self.buyer_signed_at),
            ("operator_signed_at", self.operator_signed_at),
            ("accepted_at", self.accepted_at),
        ):
            if value is not None and value < self.created_at:
                raise DomainValidationError(f"{field_name} cannot precede contract creation")

        signed_count = int(self.buyer_signed_at is not None) + int(
            self.operator_signed_at is not None
        )
        if self.status is ContractStatus.PENDING_ACCEPTANCE:
            if signed_count != 0 or self.accepted_at is not None:
                raise DomainValidationError("pending contract has inconsistent acceptance timestamps")
        elif self.status is ContractStatus.PARTIALLY_ACCEPTED:
            if signed_count != 1 or self.accepted_at is not None:
                raise DomainValidationError(
                    "partially accepted contract has inconsistent acceptance timestamps"
                )
        elif self.status is ContractStatus.ACCEPTED:
            if signed_count != 2 or self.accepted_at is None:
                raise DomainValidationError("accepted contract requires both signed timestamps")
            assert self.buyer_signed_at is not None
            assert self.operator_signed_at is not None
            if self.accepted_at != max(self.buyer_signed_at, self.operator_signed_at):
                raise DomainValidationError(
                    "accepted_at must equal the later party acceptance timestamp"
                )

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
