from __future__ import annotations

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.application.ports.tenders import TenderAuditEvent
from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.ids import EventId, TypedId
from charteros.domain.tenders import (
    Tender,
    TenderActorId,
    TenderAdminCorrection,
    TenderAdminCorrectionId,
    TenderId,
    TenderInvitation,
    TenderInvitationId,
    TenderInvitationStatus,
    TenderStatus,
)
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.tenders import (
    TenderAdminCorrectionRow,
    TenderInvitationRow,
    TenderRow,
)


def _to_tender(row: TenderRow) -> Tender:
    return Tender(
        TenderId(row.id),
        mission_id=MissionId(row.mission_id),
        status=TenderStatus(row.status),
        sealed_bid=row.sealed_bid,
        opens_at=row.opens_at,
        deadline_at=row.deadline_at,
        created_at=row.created_at,
        opened_at=row.opened_at,
        best_and_final_requested_at=row.best_and_final_requested_at,
        closed_at=row.closed_at,
        awarded_quote_id=QuoteId(row.awarded_quote_id) if row.awarded_quote_id else None,
        booking_id=BookingId(row.booking_id) if row.booking_id else None,
        awarded_at=row.awarded_at,
        version=row.version,
    )


def _to_invitation(row: TenderInvitationRow) -> TenderInvitation:
    return TenderInvitation(
        id=TenderInvitationId(row.id),
        tender_id=TenderId(row.tender_id),
        operator_id=OperatorId(row.operator_id),
        rfq_id=RfqId(row.rfq_id),
        status=TenderInvitationStatus(row.status),
        invited_at=row.invited_at,
        responded_at=row.responded_at,
        last_quote_id=QuoteId(row.last_quote_id) if row.last_quote_id else None,
        best_and_final_quote_id=(
            QuoteId(row.best_and_final_quote_id) if row.best_and_final_quote_id else None
        ),
    )


def _to_correction(row: TenderAdminCorrectionRow) -> TenderAdminCorrection:
    return TenderAdminCorrection(
        id=TenderAdminCorrectionId(row.id),
        tender_id=TenderId(row.tender_id),
        actor_id=TenderActorId(row.actor_id),
        target_type=row.target_type,
        target_id=TypedId(row.target_id),
        field_name=row.field_name,
        original_value=row.original_value,
        replacement_value=row.replacement_value,
        reason=row.reason,
        corrected_at=row.corrected_at,
        causation_event_id=EventId(row.causation_event_id),
    )


class SqlAlchemyTenderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, tender: Tender) -> None:
        self._session.add(
            TenderRow(
                id=tender.id.value,
                version=tender.version,
                mission_id=tender.mission_id.value,
                status=tender.status.value,
                sealed_bid=tender.sealed_bid,
                opens_at=tender.opens_at,
                deadline_at=tender.deadline_at,
                created_at=tender.created_at,
                opened_at=tender.opened_at,
                best_and_final_requested_at=tender.best_and_final_requested_at,
                closed_at=tender.closed_at,
                awarded_quote_id=(
                    tender.awarded_quote_id.value if tender.awarded_quote_id else None
                ),
                booking_id=tender.booking_id.value if tender.booking_id else None,
                awarded_at=tender.awarded_at,
            )
        )
        self._flush("mission already has a tender or tender conflicts with persisted state")

    def get(self, tender_id: TenderId) -> Tender | None:
        row = self._session.get(TenderRow, tender_id.value)
        return _to_tender(row) if row else None

    def get_for_update(self, tender_id: TenderId) -> Tender | None:
        row = self._session.scalar(
            select(TenderRow).where(TenderRow.id == tender_id.value).with_for_update()
        )
        return _to_tender(row) if row else None

    def get_for_mission(self, mission_id: MissionId) -> Tender | None:
        row = self._session.scalar(
            select(TenderRow).where(TenderRow.mission_id == mission_id.value)
        )
        return _to_tender(row) if row else None

    def save(self, tender: Tender, *, expected_version: int) -> None:
        statement = (
            update(TenderRow)
            .where(TenderRow.id == tender.id.value, TenderRow.version == expected_version)
            .values(
                version=tender.version,
                status=tender.status.value,
                opened_at=tender.opened_at,
                best_and_final_requested_at=tender.best_and_final_requested_at,
                closed_at=tender.closed_at,
                awarded_quote_id=(
                    tender.awarded_quote_id.value if tender.awarded_quote_id else None
                ),
                booking_id=tender.booking_id.value if tender.booking_id else None,
                awarded_at=tender.awarded_at,
            )
            .returning(TenderRow.id)
        )
        if self._session.scalar(statement) is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for tender {tender.id}"
            )
        self._session.flush()

    def add_invitation(self, invitation: TenderInvitation) -> None:
        self._session.add(
            TenderInvitationRow(
                id=invitation.id.value,
                tender_id=invitation.tender_id.value,
                operator_id=invitation.operator_id.value,
                rfq_id=invitation.rfq_id.value,
                status=invitation.status.value,
                invited_at=invitation.invited_at,
                responded_at=invitation.responded_at,
                last_quote_id=(
                    invitation.last_quote_id.value if invitation.last_quote_id else None
                ),
                best_and_final_quote_id=(
                    invitation.best_and_final_quote_id.value
                    if invitation.best_and_final_quote_id
                    else None
                ),
            )
        )
        self._flush("supplier is already invited or RFQ already belongs to a tender")

    def get_invitation(self, invitation_id: TenderInvitationId) -> TenderInvitation | None:
        row = self._session.get(TenderInvitationRow, invitation_id.value)
        return _to_invitation(row) if row else None

    def get_invitation_for_update(
        self, invitation_id: TenderInvitationId
    ) -> TenderInvitation | None:
        row = self._session.scalar(
            select(TenderInvitationRow)
            .where(TenderInvitationRow.id == invitation_id.value)
            .with_for_update()
        )
        return _to_invitation(row) if row else None

    def find_invitation_for_operator(
        self,
        tender_id: TenderId,
        operator_id: OperatorId,
    ) -> TenderInvitation | None:
        row = self._session.scalar(
            select(TenderInvitationRow).where(
                TenderInvitationRow.tender_id == tender_id.value,
                TenderInvitationRow.operator_id == operator_id.value,
            )
        )
        return _to_invitation(row) if row else None

    def find_invitation_for_rfq(self, rfq_id: RfqId) -> TenderInvitation | None:
        row = self._session.scalar(
            select(TenderInvitationRow).where(TenderInvitationRow.rfq_id == rfq_id.value)
        )
        return _to_invitation(row) if row else None

    def list_invitations(self, tender_id: TenderId) -> tuple[TenderInvitation, ...]:
        rows = self._session.scalars(
            select(TenderInvitationRow)
            .where(TenderInvitationRow.tender_id == tender_id.value)
            .order_by(TenderInvitationRow.invited_at, TenderInvitationRow.id)
        ).all()
        return tuple(_to_invitation(row) for row in rows)

    def save_invitation(self, invitation: TenderInvitation) -> None:
        statement = (
            update(TenderInvitationRow)
            .where(TenderInvitationRow.id == invitation.id.value)
            .values(
                status=invitation.status.value,
                responded_at=invitation.responded_at,
                last_quote_id=(
                    invitation.last_quote_id.value if invitation.last_quote_id else None
                ),
                best_and_final_quote_id=(
                    invitation.best_and_final_quote_id.value
                    if invitation.best_and_final_quote_id
                    else None
                ),
            )
            .returning(TenderInvitationRow.id)
        )
        if self._session.scalar(statement) is None:
            raise EntityConflictError("tender invitation disappeared during update")
        self._session.flush()

    def add_correction(self, correction: TenderAdminCorrection) -> None:
        self._session.add(
            TenderAdminCorrectionRow(
                id=correction.id.value,
                tender_id=correction.tender_id.value,
                actor_id=correction.actor_id.value,
                target_type=correction.target_type,
                target_id=correction.target_id.value,
                field_name=correction.field_name,
                original_value=correction.original_value,
                replacement_value=correction.replacement_value,
                reason=correction.reason,
                corrected_at=correction.corrected_at,
                causation_event_id=correction.causation_event_id.value,
            )
        )
        self._flush("admin correction conflicts with persisted evidence")

    def list_corrections(self, tender_id: TenderId) -> tuple[TenderAdminCorrection, ...]:
        rows = self._session.scalars(
            select(TenderAdminCorrectionRow)
            .where(TenderAdminCorrectionRow.tender_id == tender_id.value)
            .order_by(TenderAdminCorrectionRow.corrected_at, TenderAdminCorrectionRow.id)
        ).all()
        return tuple(_to_correction(row) for row in rows)

    def list_audit_events(self, tender_id: TenderId) -> tuple[TenderAuditEvent, ...]:
        tender_row = self._session.get(TenderRow, tender_id.value)
        if tender_row is None:
            return ()

        invitation_rows = self._session.scalars(
            select(TenderInvitationRow).where(TenderInvitationRow.tender_id == tender_id.value)
        ).all()
        rfq_ids = [row.rfq_id for row in invitation_rows]
        quote_ids = (
            list(
                self._session.scalars(select(QuoteRow.id).where(QuoteRow.rfq_id.in_(rfq_ids))).all()
            )
            if rfq_ids
            else []
        )

        filters = [
            and_(
                OutboxEventRow.aggregate_type == "tender",
                OutboxEventRow.aggregate_id == tender_id.value,
            ),
            and_(
                OutboxEventRow.aggregate_type == "mission",
                OutboxEventRow.aggregate_id == tender_row.mission_id,
            ),
        ]
        if rfq_ids:
            filters.append(
                and_(
                    OutboxEventRow.aggregate_type == "rfq",
                    OutboxEventRow.aggregate_id.in_(rfq_ids),
                )
            )
        if quote_ids:
            filters.append(
                and_(
                    OutboxEventRow.aggregate_type == "quote",
                    OutboxEventRow.aggregate_id.in_(quote_ids),
                )
            )
        if tender_row.booking_id is not None:
            filters.append(
                and_(
                    OutboxEventRow.aggregate_type == "booking",
                    OutboxEventRow.aggregate_id == tender_row.booking_id,
                )
            )

        rows = self._session.scalars(
            select(OutboxEventRow)
            .where(or_(*filters))
            .order_by(OutboxEventRow.recorded_at, OutboxEventRow.event_id)
        ).all()
        return tuple(
            TenderAuditEvent(
                event_id=row.event_id,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                aggregate_version=row.aggregate_version,
                event_type=row.event_type,
                occurred_at=row.occurred_at,
                recorded_at=row.recorded_at,
                actor_id=row.actor_id,
                correlation_id=row.correlation_id,
                causation_id=row.causation_id,
                canonical_json=row.canonical_json,
            )
            for row in rows
        )

    def _flush(self, message: str) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(message) from exc
