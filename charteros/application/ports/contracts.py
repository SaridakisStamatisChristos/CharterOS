from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from charteros.domain.bookings import BookingId
from charteros.domain.contracts import Contract, ContractId


class ContractRepository(Protocol):
    def add(self, contract: Contract) -> None: ...

    def get(self, contract_id: ContractId) -> Contract | None: ...

    def get_for_update(self, contract_id: ContractId) -> Contract | None: ...

    def get_for_booking(self, booking_id: BookingId) -> Contract | None: ...

    def save(self, contract: Contract, *, expected_version: int) -> None: ...


class ContractDocumentIntegration(Protocol):
    """Optional external-document validation seam for future e-signature providers."""

    def validate_document_reference(
        self,
        *,
        document_reference: str,
        document_version: int,
        metadata: Mapping[str, str],
    ) -> None: ...
