from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from charteros.application.buyer_portal import (
    BuyerProcurementAuditEvent,
    BuyerProcurementAuditPage,
)
from charteros.domain.missions import MissionId
from charteros.infrastructure.db.models.bookings import BookingRow
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.contracts import ContractRow
from charteros.infrastructure.db.models.procurement_approvals import ProcurementApprovalRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow

_SEALED_ACTIVE = ("draft", "open", "best_and_final")


class SqlAlchemyBuyerProcurementAuditRepository:
    """Metadata-only procurement timeline; PR25 owns future evidence packaging."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_mission_audit(
        self,
        mission_id: MissionId,
        *,
        limit: int,
    ) -> BuyerProcurementAuditPage:
        if not 1 <= limit <= 200:
            raise ValueError("audit limit must be between 1 and 200")

        rfq_ids = select(RfqRow.id).where(RfqRow.mission_id == mission_id.value)
        quote_ids = select(QuoteRow.id).where(QuoteRow.rfq_id.in_(rfq_ids))
        booking_ids = select(BookingRow.id).where(BookingRow.mission_id == mission_id.value)
        contract_ids = select(ContractRow.id).where(ContractRow.booking_id.in_(booking_ids))
        tender_ids = select(TenderRow.id).where(TenderRow.mission_id == mission_id.value)
        approval_ids = select(ProcurementApprovalRow.id).where(
            ProcurementApprovalRow.mission_id == mission_id.value
        )

        hidden_tender_quote_ids = (
            select(QuoteRow.id)
            .join(RfqRow, RfqRow.id == QuoteRow.rfq_id)
            .join(TenderInvitationRow, TenderInvitationRow.rfq_id == RfqRow.id)
            .join(TenderRow, TenderRow.id == TenderInvitationRow.tender_id)
            .where(
                TenderRow.mission_id == mission_id.value,
                TenderRow.sealed_bid.is_(True),
                TenderRow.status.in_(_SEALED_ACTIVE),
            )
        )

        belongs = or_(
            and_(
                OutboxEventRow.aggregate_type == "mission",
                OutboxEventRow.aggregate_id == mission_id.value,
            ),
            and_(
                OutboxEventRow.aggregate_type == "rfq",
                OutboxEventRow.aggregate_id.in_(rfq_ids),
            ),
            and_(
                OutboxEventRow.aggregate_type == "quote",
                OutboxEventRow.aggregate_id.in_(quote_ids),
                OutboxEventRow.aggregate_id.not_in(hidden_tender_quote_ids),
            ),
            and_(
                OutboxEventRow.aggregate_type == "booking",
                OutboxEventRow.aggregate_id.in_(booking_ids),
            ),
            and_(
                OutboxEventRow.aggregate_type == "contract",
                OutboxEventRow.aggregate_id.in_(contract_ids),
            ),
            and_(
                OutboxEventRow.aggregate_type == "tender",
                OutboxEventRow.aggregate_id.in_(tender_ids),
            ),
            and_(
                OutboxEventRow.aggregate_type == "procurement_approval",
                OutboxEventRow.aggregate_id.in_(approval_ids),
            ),
        )

        rows = self._session.scalars(
            select(OutboxEventRow)
            .where(belongs)
            .order_by(OutboxEventRow.recorded_at, OutboxEventRow.event_id)
            .limit(limit + 1)
        ).all()
        page = rows[:limit]
        return BuyerProcurementAuditPage(
            items=tuple(
                BuyerProcurementAuditEvent(
                    event_id=row.event_id,
                    aggregate_type=row.aggregate_type,
                    aggregate_id=row.aggregate_id,
                    aggregate_version=row.aggregate_version,
                    event_type=row.event_type,
                    event_version=row.event_version,
                    occurred_at=row.occurred_at,
                    recorded_at=row.recorded_at,
                    actor_id=row.actor_id,
                    correlation_id=row.correlation_id,
                    causation_id=row.causation_id,
                )
                for row in page
            ),
            truncated=len(rows) > limit,
        )
