from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import (
    ProcurementApproval,
    ProcurementApprovalId,
    ProcurementApprovalStatus,
)
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.infrastructure.db.models.procurement_approvals import ProcurementApprovalRow


def _to_domain(row: ProcurementApprovalRow) -> ProcurementApproval:
    return ProcurementApproval(
        ProcurementApprovalId(row.id),
        mission_id=MissionId(row.mission_id),
        buyer_id=OrganizationId(row.buyer_id),
        quote_id=QuoteId(row.quote_id),
        status=ProcurementApprovalStatus(row.status),
        approved_at=row.approved_at,
        note=row.note,
        supersedes_approval_id=(
            ProcurementApprovalId(row.supersedes_approval_id)
            if row.supersedes_approval_id is not None
            else None
        ),
        superseded_at=row.superseded_at,
        consumed_at=row.consumed_at,
        booking_id=BookingId(row.booking_id) if row.booking_id is not None else None,
        version=row.version,
    )


class SqlAlchemyProcurementApprovalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, approval: ProcurementApproval) -> None:
        self._session.add(
            ProcurementApprovalRow(
                id=approval.id.value,
                version=approval.version,
                mission_id=approval.mission_id.value,
                buyer_id=approval.buyer_id.value,
                quote_id=approval.quote_id.value,
                status=approval.status.value,
                approved_at=approval.approved_at,
                note=approval.note,
                supersedes_approval_id=(
                    approval.supersedes_approval_id.value
                    if approval.supersedes_approval_id is not None
                    else None
                ),
                superseded_at=approval.superseded_at,
                consumed_at=approval.consumed_at,
                booking_id=approval.booking_id.value if approval.booking_id is not None else None,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(
                "procurement approval conflicts with current mission approval state"
            ) from exc

    def get(self, approval_id: ProcurementApprovalId) -> ProcurementApproval | None:
        row = self._session.get(ProcurementApprovalRow, approval_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_update(
        self,
        approval_id: ProcurementApprovalId,
    ) -> ProcurementApproval | None:
        row = self._session.scalar(
            select(ProcurementApprovalRow)
            .where(ProcurementApprovalRow.id == approval_id.value)
            .with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def get_current_for_mission_for_update(
        self,
        mission_id: MissionId,
    ) -> ProcurementApproval | None:
        row = self._session.scalar(
            select(ProcurementApprovalRow)
            .where(
                ProcurementApprovalRow.mission_id == mission_id.value,
                ProcurementApprovalRow.status == ProcurementApprovalStatus.APPROVED.value,
            )
            .with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def save(self, approval: ProcurementApproval, *, expected_version: int) -> None:
        updated = self._session.scalar(
            update(ProcurementApprovalRow)
            .where(
                ProcurementApprovalRow.id == approval.id.value,
                ProcurementApprovalRow.version == expected_version,
            )
            .values(
                version=approval.version,
                status=approval.status.value,
                superseded_at=approval.superseded_at,
                consumed_at=approval.consumed_at,
                booking_id=(approval.booking_id.value if approval.booking_id is not None else None),
            )
            .returning(ProcurementApprovalRow.id)
        )
        if updated is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for procurement approval "
                f"{approval.id}"
            )
        self._session.flush()
