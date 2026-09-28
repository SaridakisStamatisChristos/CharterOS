from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import DomainEventRepository
from charteros.application.ports.contracts import (
    ContractDocumentIntegration,
    ContractRepository,
)
from charteros.application.ports.missions import MissionRepository
from charteros.domain.bookings import BookingId, BookingState
from charteros.domain.contracts import Contract, ContractId
from charteros.domain.missions import MissionStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class ContractService:
    def __init__(
        self,
        *,
        contracts: ContractRepository,
        bookings: BookingRepository,
        missions: MissionRepository,
        events: DomainEventRepository,
        document_integration: ContractDocumentIntegration | None = None,
    ) -> None:
        self._contracts = contracts
        self._bookings = bookings
        self._missions = missions
        self._events = events
        self._document_integration = document_integration

    def create_contract(
        self,
        *,
        booking_id: BookingId,
        document_reference: str,
        document_version: int,
        metadata: Mapping[str, str],
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Contract:
        created_at = _utc(now, field_name="now")
        booking = self._bookings.get_for_update(booking_id)
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        if booking.state is not BookingState.PENDING_CONTRACT:
            raise EntityConflictError("contract can only be created for a pending-contract booking")
        if self._contracts.get_for_booking(booking.id) is not None:
            raise EntityConflictError("booking already has a contract")

        mission = self._missions.get(booking.mission_id)
        if mission is None:
            raise EntityNotFoundError("booking mission does not exist")
        if mission.status is not MissionStatus.SELECTED:
            raise EntityConflictError("contract can only be created for a selected mission")

        if self._document_integration is not None:
            self._document_integration.validate_document_reference(
                document_reference=document_reference,
                document_version=document_version,
                metadata=metadata,
            )

        contract = Contract.create(
            booking_id=booking.id,
            buyer_id=mission.buyer_id,
            operator_id=booking.operator_id,
            document_reference=document_reference,
            document_version=document_version,
            metadata=metadata,
            created_at=created_at,
            correlation_id=correlation_id,
        )
        self._contracts.add(contract)
        self._events.add_aggregate_events(contract)
        return contract

    def accept_buyer(
        self,
        *,
        contract_id: ContractId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Contract:
        contract = self._get_for_update(contract_id)
        if contract.buyer_signed_at is not None:
            raise EntityConflictError("buyer has already accepted this contract")
        expected_version = contract.version
        contract.accept_buyer(
            signed_at=_utc(now, field_name="now"),
            correlation_id=correlation_id,
        )
        self._contracts.save(contract, expected_version=expected_version)
        self._events.add_aggregate_events(contract)
        return contract

    def accept_operator(
        self,
        *,
        contract_id: ContractId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Contract:
        contract = self._get_for_update(contract_id)
        if contract.operator_signed_at is not None:
            raise EntityConflictError("operator has already accepted this contract")
        expected_version = contract.version
        contract.accept_operator(
            signed_at=_utc(now, field_name="now"),
            correlation_id=correlation_id,
        )
        self._contracts.save(contract, expected_version=expected_version)
        self._events.add_aggregate_events(contract)
        return contract

    def get(self, contract_id: ContractId) -> Contract:
        contract = self._contracts.get(contract_id)
        if contract is None:
            raise EntityNotFoundError("contract does not exist")
        return contract

    def get_for_booking(self, booking_id: BookingId) -> Contract:
        if self._bookings.get(booking_id) is None:
            raise EntityNotFoundError("booking does not exist")
        contract = self._contracts.get_for_booking(booking_id)
        if contract is None:
            raise EntityNotFoundError("booking contract does not exist")
        return contract

    def _get_for_update(self, contract_id: ContractId) -> Contract:
        contract = self._contracts.get_for_update(contract_id)
        if contract is None:
            raise EntityNotFoundError("contract does not exist")
        return contract
