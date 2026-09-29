from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.bookings import BookingId
from charteros.domain.contracts import Contract, ContractId, ContractStatus
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.infrastructure.db.models.contracts import ContractRow


def _to_domain(row: ContractRow) -> Contract:
    return Contract(
        ContractId(row.id),
        booking_id=BookingId(row.booking_id),
        buyer_id=OrganizationId(row.buyer_id),
        operator_id=OperatorId(row.operator_id),
        document_reference=row.document_reference,
        document_version=row.document_version,
        metadata=row.metadata_json,
        status=ContractStatus(row.status),
        created_at=row.created_at,
        buyer_signed_at=row.buyer_signed_at,
        operator_signed_at=row.operator_signed_at,
        accepted_at=row.accepted_at,
        version=row.version,
    )


class SqlAlchemyContractRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, contract: Contract) -> None:
        self._session.add(
            ContractRow(
                id=contract.id.value,
                version=contract.version,
                booking_id=contract.booking_id.value,
                buyer_id=contract.buyer_id.value,
                operator_id=contract.operator_id.value,
                document_reference=contract.document_reference,
                document_version=contract.document_version,
                metadata_json=dict(contract.metadata),
                status=contract.status.value,
                created_at=contract.created_at,
                buyer_signed_at=contract.buyer_signed_at,
                operator_signed_at=contract.operator_signed_at,
                accepted_at=contract.accepted_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError("booking already has a contract") from exc

    def get(self, contract_id: ContractId) -> Contract | None:
        row = self._session.get(ContractRow, contract_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_update(self, contract_id: ContractId) -> Contract | None:
        row = self._session.scalar(
            select(ContractRow).where(ContractRow.id == contract_id.value).with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def get_for_booking(self, booking_id: BookingId) -> Contract | None:
        row = self._session.scalar(
            select(ContractRow).where(ContractRow.booking_id == booking_id.value)
        )
        return _to_domain(row) if row is not None else None

    def get_for_booking_for_update(self, booking_id: BookingId) -> Contract | None:
        row = self._session.scalar(
            select(ContractRow)
            .where(ContractRow.booking_id == booking_id.value)
            .with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def save(self, contract: Contract, *, expected_version: int) -> None:
        statement = (
            update(ContractRow)
            .where(
                ContractRow.id == contract.id.value,
                ContractRow.version == expected_version,
            )
            .values(
                version=contract.version,
                status=contract.status.value,
                buyer_signed_at=contract.buyer_signed_at,
                operator_signed_at=contract.operator_signed_at,
                accepted_at=contract.accepted_at,
            )
            .returning(ContractRow.id)
        )
        updated_id = self._session.scalar(statement)
        if updated_id is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for contract {contract.id}"
            )
        self._session.flush()
