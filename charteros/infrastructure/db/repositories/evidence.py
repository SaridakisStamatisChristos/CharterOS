from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from charteros.application.evidence import (
    SNAPSHOT_SCHEMA_VERSION,
    DecisionEvidenceRecord,
    EvidenceEventRecord,
    EvidenceMaterial,
    EvidenceParty,
    EvidenceSourceRecord,
    EvidenceSubjectType,
    canonical_digest,
    canonical_json,
)
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.infrastructure.db.models.bookings import BookingRow
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.contracts import ContractRow
from charteros.infrastructure.db.models.disruptions import (
    DisruptionBuyerDecisionRow,
    DisruptionCommercialChangeRow,
    DisruptionProposalRow,
    DisruptionRow,
)
from charteros.infrastructure.db.models.evidence import DecisionEvidenceSnapshotRow
from charteros.infrastructure.db.models.fx import FxLockConversionRow, FxLockRow, FxRateRow
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.procurement_approvals import ProcurementApprovalRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.reconciliation import (
    FinancialReconciliationRow,
    OperatorInvoiceLineRow,
    OperatorInvoiceRevisionRow,
    ReconciliationDisputeRow,
    VarianceApprovalRow,
)
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow

_ACTIVE_SEALED_TENDER_STATES = ("draft", "open", "best_and_final")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EntityConflictError("evidence timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _record(
    source_type: str,
    source_id: UUID,
    version: int | None,
    facts: Mapping[str, object],
) -> EvidenceSourceRecord:
    return EvidenceSourceRecord(
        source_type=source_type,
        source_id=source_id,
        version=version,
        facts=dict(facts),
    )


def _ids(rows: Iterable[object], attribute: str = "id") -> set[UUID]:
    result: set[UUID] = set()
    for row in rows:
        value = getattr(row, attribute)
        if not isinstance(value, UUID):
            raise EntityConflictError("evidence source contains a non-UUID identity")
        result.add(value)
    return result


class SqlAlchemyDecisionEvidenceRepository:
    """Append-only snapshots for computed decisions that are not domain aggregates."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_snapshot(
        self,
        *,
        decision_type: str,
        subject_type: str,
        subject_id: UUID,
        source_aggregate_type: str,
        source_aggregate_id: UUID,
        decided_at: datetime,
        known_as_of: datetime | None,
        actor_id: UUID | None,
        correlation_id: UUID | None,
        policy_versions: Mapping[str, object],
        content: Mapping[str, object],
    ) -> UUID:
        snapshot_id = uuid4()
        when = _utc(decided_at)
        cutoff = _utc(known_as_of) if known_as_of is not None else None
        body: dict[str, object] = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "decision_type": decision_type,
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "source_aggregate_type": source_aggregate_type,
            "source_aggregate_id": str(source_aggregate_id),
            "decided_at": when,
            "known_as_of": cutoff,
            "actor_id": str(actor_id) if actor_id is not None else None,
            "correlation_id": str(correlation_id) if correlation_id is not None else None,
            "policy_versions": dict(policy_versions),
            "content": dict(content),
        }
        self._session.add(
            DecisionEvidenceSnapshotRow(
                id=snapshot_id,
                decision_type=decision_type,
                subject_type=subject_type,
                subject_id=subject_id,
                source_aggregate_type=source_aggregate_type,
                source_aggregate_id=source_aggregate_id,
                schema_version=SNAPSHOT_SCHEMA_VERSION,
                decided_at=when,
                known_as_of=cutoff,
                actor_id=actor_id,
                correlation_id=correlation_id,
                policy_versions=dict(policy_versions),
                canonical_json=canonical_json(body),
                integrity_digest=canonical_digest(body),
            )
        )
        return snapshot_id


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load(
        self,
        *,
        subject_type: EvidenceSubjectType,
        subject_id: UUID,
        party: EvidenceParty,
        event_limit: int,
    ) -> EvidenceMaterial:
        mission, target_booking_id = self._resolve_subject(
            subject_type=subject_type,
            subject_id=subject_id,
        )
        self._authorize(
            subject_type=subject_type,
            mission=mission,
            target_booking_id=target_booking_id,
            party=party,
        )

        rfqs = list(
            self._session.scalars(
                select(RfqRow)
                .where(RfqRow.mission_id == mission.id)
                .order_by(RfqRow.created_at, RfqRow.id)
            ).all()
        )
        tenders = list(
            self._session.scalars(
                select(TenderRow)
                .where(TenderRow.mission_id == mission.id)
                .order_by(TenderRow.created_at, TenderRow.id)
            ).all()
        )
        active_sealed = [
            tender
            for tender in tenders
            if tender.sealed_bid and tender.status in _ACTIVE_SEALED_TENDER_STATES
        ]
        invitation_rows: list[TenderInvitationRow] = []
        if active_sealed:
            invitation_rows = list(
                self._session.scalars(
                    select(TenderInvitationRow)
                    .where(
                        TenderInvitationRow.tender_id.in_([tender.id for tender in active_sealed])
                    )
                    .order_by(TenderInvitationRow.invited_at, TenderInvitationRow.id)
                ).all()
            )
        active_invitation_rfq_ids = {row.rfq_id for row in invitation_rows}

        if party.operator_id is not None:
            rfqs = [row for row in rfqs if row.operator_id == party.operator_id]
        elif active_sealed:
            rfqs = [row for row in rfqs if row.id not in active_invitation_rfq_ids]

        rfq_ids = _ids(rfqs)
        quotes: list[QuoteRow] = []
        if rfq_ids:
            quotes = list(
                self._session.scalars(
                    select(QuoteRow)
                    .where(QuoteRow.rfq_id.in_(rfq_ids))
                    .order_by(QuoteRow.submitted_at, QuoteRow.revision_number, QuoteRow.id)
                ).all()
            )
        quote_ids = _ids(quotes)

        approvals = list(
            self._session.scalars(
                select(ProcurementApprovalRow)
                .where(ProcurementApprovalRow.mission_id == mission.id)
                .order_by(ProcurementApprovalRow.approved_at, ProcurementApprovalRow.id)
            ).all()
        )
        if party.operator_id is not None:
            approvals = [row for row in approvals if row.quote_id in quote_ids]

        fx_locks: list[FxLockRow] = []
        fx_conversions: list[FxLockConversionRow] = []
        fx_rates: list[FxRateRow] = []
        if party.buyer_id is not None:
            fx_locks = list(
                self._session.scalars(
                    select(FxLockRow)
                    .where(
                        FxLockRow.mission_id == mission.id,
                        FxLockRow.buyer_id == party.buyer_id,
                        FxLockRow.status == "consumed",
                    )
                    .order_by(FxLockRow.locked_at, FxLockRow.id)
                ).all()
            )
            fx_lock_ids = _ids(fx_locks)
            if fx_lock_ids:
                fx_conversions = list(
                    self._session.scalars(
                        select(FxLockConversionRow)
                        .where(FxLockConversionRow.lock_id.in_(fx_lock_ids))
                        .order_by(
                            FxLockConversionRow.lock_id,
                            FxLockConversionRow.global_rank,
                            FxLockConversionRow.quote_id,
                        )
                    ).all()
                )
                fx_rate_ids = {
                    row.rate_id for row in fx_conversions if row.rate_id is not None
                }
                if fx_rate_ids:
                    fx_rates = list(
                        self._session.scalars(
                            select(FxRateRow)
                            .where(FxRateRow.id.in_(fx_rate_ids))
                            .order_by(
                                FxRateRow.fx_timestamp,
                                FxRateRow.recorded_at,
                                FxRateRow.id,
                            )
                        ).all()
                    )

        bookings = list(
            self._session.scalars(
                select(BookingRow)
                .where(BookingRow.mission_id == mission.id)
                .order_by(BookingRow.created_at, BookingRow.id)
            ).all()
        )
        if party.operator_id is not None:
            bookings = [row for row in bookings if row.operator_id == party.operator_id]
        booking_ids = _ids(bookings)

        contracts: list[ContractRow] = []
        disruptions: list[DisruptionRow] = []
        reconciliations: list[FinancialReconciliationRow] = []
        if booking_ids:
            contracts = list(
                self._session.scalars(
                    select(ContractRow)
                    .where(ContractRow.booking_id.in_(booking_ids))
                    .order_by(ContractRow.created_at, ContractRow.id)
                ).all()
            )
            disruptions = list(
                self._session.scalars(
                    select(DisruptionRow)
                    .where(DisruptionRow.booking_id.in_(booking_ids))
                    .order_by(DisruptionRow.detected_at, DisruptionRow.id)
                ).all()
            )
            reconciliations = list(
                self._session.scalars(
                    select(FinancialReconciliationRow)
                    .where(FinancialReconciliationRow.booking_id.in_(booking_ids))
                    .order_by(FinancialReconciliationRow.opened_at, FinancialReconciliationRow.id)
                ).all()
            )

        disruption_ids = _ids(disruptions)
        proposals: list[DisruptionProposalRow] = []
        commercial_changes: list[DisruptionCommercialChangeRow] = []
        buyer_decisions: list[DisruptionBuyerDecisionRow] = []
        if disruption_ids:
            proposals = list(
                self._session.scalars(
                    select(DisruptionProposalRow)
                    .where(DisruptionProposalRow.disruption_id.in_(disruption_ids))
                    .order_by(
                        DisruptionProposalRow.disruption_id,
                        DisruptionProposalRow.revision_number,
                        DisruptionProposalRow.id,
                    )
                ).all()
            )
            commercial_changes = list(
                self._session.scalars(
                    select(DisruptionCommercialChangeRow)
                    .where(DisruptionCommercialChangeRow.disruption_id.in_(disruption_ids))
                    .order_by(
                        DisruptionCommercialChangeRow.disruption_id,
                        DisruptionCommercialChangeRow.revision_number,
                        DisruptionCommercialChangeRow.id,
                    )
                ).all()
            )
            buyer_decisions = list(
                self._session.scalars(
                    select(DisruptionBuyerDecisionRow)
                    .where(DisruptionBuyerDecisionRow.disruption_id.in_(disruption_ids))
                    .order_by(
                        DisruptionBuyerDecisionRow.decided_at,
                        DisruptionBuyerDecisionRow.id,
                    )
                ).all()
            )

        reconciliation_ids = _ids(reconciliations)
        invoices: list[OperatorInvoiceRevisionRow] = []
        disputes: list[ReconciliationDisputeRow] = []
        variance_approvals: list[VarianceApprovalRow] = []
        invoice_lines: list[OperatorInvoiceLineRow] = []
        if reconciliation_ids:
            invoices = list(
                self._session.scalars(
                    select(OperatorInvoiceRevisionRow)
                    .where(OperatorInvoiceRevisionRow.reconciliation_id.in_(reconciliation_ids))
                    .order_by(
                        OperatorInvoiceRevisionRow.reconciliation_id,
                        OperatorInvoiceRevisionRow.revision_number,
                        OperatorInvoiceRevisionRow.id,
                    )
                ).all()
            )
            disputes = list(
                self._session.scalars(
                    select(ReconciliationDisputeRow)
                    .where(ReconciliationDisputeRow.reconciliation_id.in_(reconciliation_ids))
                    .order_by(
                        ReconciliationDisputeRow.opened_at,
                        ReconciliationDisputeRow.id,
                    )
                ).all()
            )
            variance_approvals = list(
                self._session.scalars(
                    select(VarianceApprovalRow)
                    .where(VarianceApprovalRow.reconciliation_id.in_(reconciliation_ids))
                    .order_by(VarianceApprovalRow.approved_at, VarianceApprovalRow.id)
                ).all()
            )
            invoice_ids = _ids(invoices)
            if invoice_ids:
                invoice_lines = list(
                    self._session.scalars(
                        select(OperatorInvoiceLineRow)
                        .where(OperatorInvoiceLineRow.invoice_revision_id.in_(invoice_ids))
                        .order_by(
                            OperatorInvoiceLineRow.invoice_revision_id,
                            OperatorInvoiceLineRow.line_number,
                        )
                    ).all()
                )

        self._validate_lineage(
            mission=mission,
            quotes=quotes,
            approvals=approvals,
            bookings=bookings,
            contracts=contracts,
            disruptions=disruptions,
            proposals=proposals,
            commercial_changes=commercial_changes,
            buyer_decisions=buyer_decisions,
            reconciliations=reconciliations,
            invoices=invoices,
            invoice_lines=invoice_lines,
            disputes=disputes,
            variance_approvals=variance_approvals,
            fx_locks=fx_locks,
            fx_conversions=fx_conversions,
            fx_rates=fx_rates,
        )

        sources = self._sources(
            mission=mission,
            rfqs=rfqs,
            quotes=quotes,
            approvals=approvals,
            bookings=bookings,
            contracts=contracts,
            tenders=(
                []
                if party.operator_id is not None or (active_sealed and party.buyer_id is not None)
                else tenders
            ),
            disruptions=disruptions,
            proposals=proposals,
            commercial_changes=commercial_changes,
            buyer_decisions=buyer_decisions,
            reconciliations=reconciliations,
            invoices=invoices,
            invoice_lines=invoice_lines,
            disputes=disputes,
            variance_approvals=variance_approvals,
            fx_locks=fx_locks,
            fx_conversions=fx_conversions,
            fx_rates=fx_rates,
        )

        aggregate_keys = {
            (source.source_type, source.source_id)
            for source in sources
            if source.version is not None
        }
        conditions = [
            and_(
                OutboxEventRow.aggregate_type == aggregate_type,
                OutboxEventRow.aggregate_id == aggregate_id,
            )
            for aggregate_type, aggregate_id in sorted(
                aggregate_keys,
                key=lambda item: (item[0], item[1].hex),
            )
        ]
        event_rows: list[OutboxEventRow] = []
        if conditions:
            event_rows = list(
                self._session.scalars(
                    select(OutboxEventRow)
                    .where(or_(*conditions))
                    .order_by(OutboxEventRow.recorded_at, OutboxEventRow.event_id)
                    .limit(event_limit + 1)
                ).all()
            )
        truncated = len(event_rows) > event_limit
        event_rows = event_rows[:event_limit]
        events = tuple(
            EvidenceEventRecord(
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
                canonical_json=row.canonical_json,
            )
            for row in event_rows
        )

        snapshot_rows = list(
            self._session.scalars(
                select(DecisionEvidenceSnapshotRow)
                .where(
                    DecisionEvidenceSnapshotRow.subject_type == "mission",
                    DecisionEvidenceSnapshotRow.subject_id == mission.id,
                )
                .order_by(
                    DecisionEvidenceSnapshotRow.decided_at,
                    DecisionEvidenceSnapshotRow.id,
                )
            ).all()
        )
        decisions = tuple(
            self._decision_record(row)
            for row in snapshot_rows
            if (row.source_aggregate_type, row.source_aggregate_id) in aggregate_keys
            and not (party.operator_id is not None and row.decision_type == "quote_comparison")
        )

        return EvidenceMaterial(
            sources=tuple(sources),
            events=events,
            decisions=decisions,
            truncated=truncated,
        )

    def _resolve_subject(
        self,
        *,
        subject_type: EvidenceSubjectType,
        subject_id: UUID,
    ) -> tuple[MissionRow, UUID | None]:
        booking_id: UUID | None = None
        mission_id: UUID

        if subject_type is EvidenceSubjectType.MISSION:
            mission_id = subject_id
        elif subject_type is EvidenceSubjectType.BOOKING:
            booking = self._session.get(BookingRow, subject_id)
            if booking is None:
                raise EntityNotFoundError("booking evidence subject does not exist")
            mission_id = booking.mission_id
            booking_id = booking.id
        elif subject_type is EvidenceSubjectType.DISRUPTION:
            disruption = self._session.get(DisruptionRow, subject_id)
            if disruption is None:
                raise EntityNotFoundError("disruption evidence subject does not exist")
            booking = self._session.get(BookingRow, disruption.booking_id)
            if booking is None:
                raise EntityConflictError("disruption references a missing booking")
            mission_id = booking.mission_id
            booking_id = booking.id
        elif subject_type is EvidenceSubjectType.RECONCILIATION:
            reconciliation = self._session.get(FinancialReconciliationRow, subject_id)
            if reconciliation is None:
                raise EntityNotFoundError("reconciliation evidence subject does not exist")
            booking = self._session.get(BookingRow, reconciliation.booking_id)
            if booking is None:
                raise EntityConflictError("reconciliation references a missing booking")
            mission_id = booking.mission_id
            booking_id = booking.id
        else:
            raise ValueError(f"unsupported evidence subject type {subject_type}")

        mission = self._session.get(MissionRow, mission_id)
        if mission is None:
            raise EntityConflictError("evidence subject references a missing mission")
        return mission, booking_id

    def _authorize(
        self,
        *,
        subject_type: EvidenceSubjectType,
        mission: MissionRow,
        target_booking_id: UUID | None,
        party: EvidenceParty,
    ) -> None:
        if party.buyer_id is not None:
            if mission.buyer_id != party.buyer_id:
                raise EntityNotFoundError("evidence subject does not exist")
            return

        assert party.operator_id is not None
        if subject_type is EvidenceSubjectType.MISSION:
            raise EntityNotFoundError("mission evidence requires buyer context")
        if target_booking_id is None:
            raise EntityNotFoundError("evidence subject does not exist")
        booking = self._session.get(BookingRow, target_booking_id)
        if booking is None or booking.operator_id != party.operator_id:
            raise EntityNotFoundError("evidence subject does not exist")

    @staticmethod
    def _validate_lineage(
        *,
        mission: MissionRow,
        quotes: list[QuoteRow],
        approvals: list[ProcurementApprovalRow],
        bookings: list[BookingRow],
        contracts: list[ContractRow],
        disruptions: list[DisruptionRow],
        proposals: list[DisruptionProposalRow],
        commercial_changes: list[DisruptionCommercialChangeRow],
        buyer_decisions: list[DisruptionBuyerDecisionRow],
        reconciliations: list[FinancialReconciliationRow],
        invoices: list[OperatorInvoiceRevisionRow],
        invoice_lines: list[OperatorInvoiceLineRow],
        disputes: list[ReconciliationDisputeRow],
        variance_approvals: list[VarianceApprovalRow],
    ) -> None:
        quote_by_id = {row.id: row for row in quotes}
        booking_by_id = {row.id: row for row in bookings}
        proposal_by_id = {row.id: row for row in proposals}
        change_by_id = {row.id: row for row in commercial_changes}
        decision_by_id = {row.id: row for row in buyer_decisions}
        invoice_by_id = {row.id: row for row in invoices}
        dispute_by_id = {row.id: row for row in disputes}
        approval_by_id = {row.id: row for row in variance_approvals}

        for booking in bookings:
            if booking.mission_id != mission.id:
                raise EntityConflictError("booking mission lineage conflicts")
            quote = quote_by_id.get(booking.accepted_quote_id)
            if quote is None:
                raise EntityConflictError(
                    f"booking {booking.id} accepted quote evidence is missing"
                )
            if quote.status != "accepted":
                raise EntityConflictError(
                    f"booking {booking.id} accepted quote is not canonically accepted"
                )

        for approval in approvals:
            quote = quote_by_id.get(approval.quote_id)
            if quote is None:
                raise EntityConflictError(
                    f"procurement approval {approval.id} quote evidence is missing"
                )
            if approval.booking_id is not None:
                approval_booking = booking_by_id.get(approval.booking_id)
                if approval_booking is None:
                    raise EntityConflictError(
                        f"procurement approval {approval.id} booking evidence is missing"
                    )
                if approval_booking.accepted_quote_id != approval.quote_id:
                    raise EntityConflictError(
                        f"procurement approval {approval.id} conflicts with booking quote"
                    )

        for contract in contracts:
            if contract.booking_id not in booking_by_id:
                raise EntityConflictError(f"contract {contract.id} booking evidence is missing")

        for disruption in disruptions:
            if disruption.booking_id not in booking_by_id:
                raise EntityConflictError(f"disruption {disruption.id} booking evidence is missing")
            if disruption.status == "resolved":
                if (
                    disruption.selected_proposal_id is None
                    or disruption.selected_proposal_id not in proposal_by_id
                ):
                    raise EntityConflictError(
                        f"resolved disruption {disruption.id} selected proposal is missing"
                    )
                proposal = proposal_by_id[disruption.selected_proposal_id]
                if proposal.disruption_id != disruption.id:
                    raise EntityConflictError(
                        f"resolved disruption {disruption.id} selected proposal conflicts"
                    )
                if disruption.selected_commercial_change_id is not None:
                    change = change_by_id.get(disruption.selected_commercial_change_id)
                    if change is None or change.disruption_id != disruption.id:
                        raise EntityConflictError(
                            f"resolved disruption {disruption.id} commercial evidence conflicts"
                        )
                if disruption.selected_buyer_decision_id is not None:
                    decision = decision_by_id.get(disruption.selected_buyer_decision_id)
                    if decision is None or decision.disruption_id != disruption.id:
                        raise EntityConflictError(
                            f"resolved disruption {disruption.id} buyer decision conflicts"
                        )

        lines_by_invoice: dict[UUID, list[OperatorInvoiceLineRow]] = {}
        for line in invoice_lines:
            lines_by_invoice.setdefault(line.invoice_revision_id, []).append(line)
        for invoice in invoices:
            lines = lines_by_invoice.get(invoice.id, [])
            if not lines:
                raise EntityConflictError(
                    f"invoice {invoice.id} immutable line evidence is missing"
                )
            if sum(line.amount_minor for line in lines) != invoice.total_amount_minor:
                raise EntityConflictError(
                    f"invoice {invoice.id} line evidence does not sum to invoice total"
                )

        for reconciliation in reconciliations:
            reconciliation_booking = booking_by_id.get(reconciliation.booking_id)
            if reconciliation_booking is None:
                raise EntityConflictError(
                    f"reconciliation {reconciliation.id} booking evidence is missing"
                )
            if reconciliation_booking.accepted_quote_id != reconciliation.accepted_quote_id:
                raise EntityConflictError(
                    f"reconciliation {reconciliation.id} accepted quote conflicts"
                )
            if reconciliation.current_invoice_revision_id is not None:
                current = invoice_by_id.get(reconciliation.current_invoice_revision_id)
                if current is None or current.reconciliation_id != reconciliation.id:
                    raise EntityConflictError(
                        f"reconciliation {reconciliation.id} current invoice conflicts"
                    )
            if reconciliation.current_dispute_id is not None:
                dispute = dispute_by_id.get(reconciliation.current_dispute_id)
                if dispute is None or dispute.reconciliation_id != reconciliation.id:
                    raise EntityConflictError(
                        f"reconciliation {reconciliation.id} current dispute conflicts"
                    )
            if reconciliation.current_variance_approval_id is not None:
                current_variance_approval = approval_by_id.get(
                    reconciliation.current_variance_approval_id
                )
                if (
                    current_variance_approval is None
                    or current_variance_approval.reconciliation_id != reconciliation.id
                ):
                    raise EntityConflictError(
                        f"reconciliation {reconciliation.id} variance approval conflicts"
                    )
            if reconciliation.status == "completed":
                if (
                    reconciliation.final_invoice_revision_id is None
                    or reconciliation.final_payable_minor is None
                    or reconciliation.approved_variance_minor is None
                ):
                    raise EntityConflictError(
                        f"completed reconciliation {reconciliation.id} terminal evidence is missing"
                    )
                final_invoice = invoice_by_id.get(reconciliation.final_invoice_revision_id)
                if final_invoice is None or final_invoice.reconciliation_id != reconciliation.id:
                    raise EntityConflictError(
                        f"completed reconciliation {reconciliation.id} final invoice conflicts"
                    )
                expected_payable = (
                    final_invoice.total_amount_minor
                    if final_invoice.variance_minor <= 0
                    else reconciliation.booked_amount_minor + reconciliation.approved_variance_minor
                )
                if reconciliation.final_payable_minor != expected_payable:
                    raise EntityConflictError(
                        f"completed reconciliation {reconciliation.id} final payable conflicts"
                    )
                if reconciliation.final_payable_minor > final_invoice.total_amount_minor:
                    raise EntityConflictError(
                        f"completed reconciliation {reconciliation.id} payable exceeds invoice"
                    )

    @staticmethod
    def _sources(
        *,
        mission: MissionRow,
        rfqs: list[RfqRow],
        quotes: list[QuoteRow],
        approvals: list[ProcurementApprovalRow],
        bookings: list[BookingRow],
        contracts: list[ContractRow],
        tenders: list[TenderRow],
        disruptions: list[DisruptionRow],
        proposals: list[DisruptionProposalRow],
        commercial_changes: list[DisruptionCommercialChangeRow],
        buyer_decisions: list[DisruptionBuyerDecisionRow],
        reconciliations: list[FinancialReconciliationRow],
        invoices: list[OperatorInvoiceRevisionRow],
        invoice_lines: list[OperatorInvoiceLineRow],
        disputes: list[ReconciliationDisputeRow],
        variance_approvals: list[VarianceApprovalRow],
    ) -> list[EvidenceSourceRecord]:
        sources: list[EvidenceSourceRecord] = [
            _record(
                "mission",
                mission.id,
                mission.version,
                {
                    "buyer_id": mission.buyer_id,
                    "origin_airport_id": mission.origin_airport_id,
                    "destination_airport_id": mission.destination_airport_id,
                    "departure_from": mission.departure_from,
                    "departure_to": mission.departure_to,
                    "passenger_count": mission.passenger_count,
                    "max_budget_amount_minor": mission.max_budget_amount_minor,
                    "max_budget_currency": mission.max_budget_currency,
                    "status": mission.status,
                },
            )
        ]
        for rfq in rfqs:
            sources.append(
                _record(
                    "rfq",
                    rfq.id,
                    rfq.version,
                    {
                        "mission_id": rfq.mission_id,
                        "operator_id": rfq.operator_id,
                        "status": rfq.status,
                        "created_at": rfq.created_at,
                        "sent_at": rfq.sent_at,
                        "response_deadline": rfq.response_deadline,
                    },
                )
            )
        for quote in quotes:
            sources.append(
                _record(
                    "quote",
                    quote.id,
                    quote.version,
                    {
                        "rfq_id": quote.rfq_id,
                        "aircraft_id": quote.aircraft_id,
                        "currency": quote.currency,
                        "base_amount_minor": quote.base_amount_minor,
                        "repositioning_amount_minor": quote.repositioning_amount_minor,
                        "price_components": [
                            {
                                "line_number": component.line_number,
                                "category": component.category,
                                "label": component.label,
                                "amount_minor": component.amount_minor,
                                "applicability": component.applicability,
                                "condition": component.condition,
                            }
                            for component in quote.components
                        ],
                        "revision_number": quote.revision_number,
                        "supersedes_quote_id": quote.supersedes_quote_id,
                        "status": quote.status,
                        "submitted_at": quote.submitted_at,
                        "valid_until": quote.valid_until,
                        "accepted_at": quote.accepted_at,
                    },
                )
            )
        for procurement_approval in approvals:
            sources.append(
                _record(
                    "procurement_approval",
                    procurement_approval.id,
                    procurement_approval.version,
                    {
                        "mission_id": procurement_approval.mission_id,
                        "buyer_id": procurement_approval.buyer_id,
                        "quote_id": procurement_approval.quote_id,
                        "status": procurement_approval.status,
                        "approved_at": procurement_approval.approved_at,
                        "supersedes_approval_id": procurement_approval.supersedes_approval_id,
                        "superseded_at": procurement_approval.superseded_at,
                        "consumed_at": procurement_approval.consumed_at,
                        "booking_id": procurement_approval.booking_id,
                        "note": procurement_approval.note,
                    },
                )
            )
        for booking in bookings:
            sources.append(
                _record(
                    "booking",
                    booking.id,
                    booking.version,
                    {
                        "mission_id": booking.mission_id,
                        "accepted_quote_id": booking.accepted_quote_id,
                        "operator_id": booking.operator_id,
                        "aircraft_id": booking.aircraft_id,
                        "state": booking.state,
                        "created_at": booking.created_at,
                        "state_changed_at": booking.state_changed_at,
                    },
                )
            )
        for contract in contracts:
            sources.append(
                _record(
                    "contract",
                    contract.id,
                    contract.version,
                    {
                        "booking_id": contract.booking_id,
                        "buyer_id": contract.buyer_id,
                        "operator_id": contract.operator_id,
                        "document_reference": contract.document_reference,
                        "document_version": contract.document_version,
                        "status": contract.status,
                        "created_at": contract.created_at,
                        "buyer_signed_at": contract.buyer_signed_at,
                        "operator_signed_at": contract.operator_signed_at,
                        "accepted_at": contract.accepted_at,
                    },
                )
            )
        for tender in tenders:
            sources.append(
                _record(
                    "tender",
                    tender.id,
                    tender.version,
                    {
                        "mission_id": tender.mission_id,
                        "status": tender.status,
                        "sealed_bid": tender.sealed_bid,
                        "opens_at": tender.opens_at,
                        "deadline_at": tender.deadline_at,
                        "awarded_quote_id": tender.awarded_quote_id,
                        "booking_id": tender.booking_id,
                        "awarded_at": tender.awarded_at,
                    },
                )
            )
        for disruption in disruptions:
            sources.append(
                _record(
                    "disruption",
                    disruption.id,
                    disruption.version,
                    {
                        "booking_id": disruption.booking_id,
                        "disruption_type": disruption.disruption_type,
                        "status": disruption.status,
                        "detected_at": disruption.detected_at,
                        "effective_at": disruption.effective_at,
                        "reason": disruption.reason,
                        "current_proposal_id": disruption.current_proposal_id,
                        "current_commercial_change_id": disruption.current_commercial_change_id,
                        "latest_buyer_decision_id": disruption.latest_buyer_decision_id,
                        "selected_proposal_id": disruption.selected_proposal_id,
                        "selected_commercial_change_id": disruption.selected_commercial_change_id,
                        "selected_buyer_decision_id": disruption.selected_buyer_decision_id,
                        "resolved_at": disruption.resolved_at,
                        "resolution_outcome": disruption.resolution_outcome,
                    },
                )
            )
        for proposal in proposals:
            sources.append(
                _record(
                    "disruption_proposal",
                    proposal.id,
                    None,
                    {
                        "disruption_id": proposal.disruption_id,
                        "revision_number": proposal.revision_number,
                        "supersedes_proposal_id": proposal.supersedes_proposal_id,
                        "status": proposal.status,
                        "proposed_operator_id": proposal.proposed_operator_id,
                        "proposed_aircraft_id": proposal.proposed_aircraft_id,
                        "proposed_operator_version": proposal.proposed_operator_version,
                        "proposed_aircraft_version": proposal.proposed_aircraft_version,
                        "availability_record_id": proposal.availability_record_id,
                        "availability_recorded_at": proposal.availability_recorded_at,
                        "departure_start": proposal.departure_start,
                        "departure_end": proposal.departure_end,
                        "requires_buyer_decision": proposal.requires_buyer_decision,
                        "source": proposal.source,
                        "source_evidence": proposal.source_evidence,
                        "proposed_at": proposal.proposed_at,
                        "superseded_at": proposal.superseded_at,
                    },
                )
            )
        for commercial_change in commercial_changes:
            sources.append(
                _record(
                    "disruption_commercial_change",
                    commercial_change.id,
                    None,
                    {
                        "disruption_id": commercial_change.disruption_id,
                        "proposal_id": commercial_change.proposal_id,
                        "revision_number": commercial_change.revision_number,
                        "supersedes_change_id": commercial_change.supersedes_change_id,
                        "status": commercial_change.status,
                        "original_quote_id": commercial_change.original_quote_id,
                        "currency": commercial_change.currency,
                        "normalization_version": commercial_change.normalization_version,
                        "original_expected_total_minor": (
                            commercial_change.original_expected_total_minor
                        ),
                        "original_worst_case_total_minor": (
                            commercial_change.original_worst_case_total_minor
                        ),
                        "known_adjustment_minor": commercial_change.known_adjustment_minor,
                        "conditional_adjustment_minor": (
                            commercial_change.conditional_adjustment_minor
                        ),
                        "resulting_expected_total_minor": (
                            commercial_change.resulting_expected_total_minor
                        ),
                        "resulting_worst_case_total_minor": (
                            commercial_change.resulting_worst_case_total_minor
                        ),
                        "terms_summary": commercial_change.terms_summary,
                        "created_at": commercial_change.created_at,
                        "superseded_at": commercial_change.superseded_at,
                    },
                )
            )
        for buyer_decision in buyer_decisions:
            sources.append(
                _record(
                    "disruption_buyer_decision",
                    buyer_decision.id,
                    None,
                    {
                        "disruption_id": buyer_decision.disruption_id,
                        "proposal_id": buyer_decision.proposal_id,
                        "commercial_change_id": buyer_decision.commercial_change_id,
                        "buyer_id": buyer_decision.buyer_id,
                        "decision": buyer_decision.decision,
                        "decided_at": buyer_decision.decided_at,
                        "note": buyer_decision.note,
                    },
                )
            )

        lines_by_invoice: dict[UUID, list[OperatorInvoiceLineRow]] = {}
        for line in invoice_lines:
            lines_by_invoice.setdefault(line.invoice_revision_id, []).append(line)
        for reconciliation in reconciliations:
            sources.append(
                _record(
                    "financial_reconciliation",
                    reconciliation.id,
                    reconciliation.version,
                    {
                        "booking_id": reconciliation.booking_id,
                        "accepted_quote_id": reconciliation.accepted_quote_id,
                        "buyer_id": reconciliation.buyer_id,
                        "operator_id": reconciliation.operator_id,
                        "currency": reconciliation.currency,
                        "quote_normalization_version": reconciliation.quote_normalization_version,
                        "quote_revision_number": reconciliation.quote_revision_number,
                        "booked_amount_minor": reconciliation.booked_amount_minor,
                        "booked_worst_case_amount_minor": (
                            reconciliation.booked_worst_case_amount_minor
                        ),
                        "opened_at": reconciliation.opened_at,
                        "status": reconciliation.status,
                        "current_invoice_revision_id": reconciliation.current_invoice_revision_id,
                        "current_dispute_id": reconciliation.current_dispute_id,
                        "current_variance_approval_id": reconciliation.current_variance_approval_id,
                        "final_invoice_revision_id": reconciliation.final_invoice_revision_id,
                        "approved_variance_minor": reconciliation.approved_variance_minor,
                        "final_payable_minor": reconciliation.final_payable_minor,
                        "completed_at": reconciliation.completed_at,
                    },
                )
            )
        for invoice in invoices:
            sources.append(
                _record(
                    "operator_invoice_revision",
                    invoice.id,
                    None,
                    {
                        "reconciliation_id": invoice.reconciliation_id,
                        "revision_number": invoice.revision_number,
                        "supersedes_invoice_revision_id": invoice.supersedes_invoice_revision_id,
                        "status": invoice.status,
                        "invoice_reference": invoice.invoice_reference,
                        "currency": invoice.currency,
                        "booked_amount_minor": invoice.booked_amount_minor,
                        "total_amount_minor": invoice.total_amount_minor,
                        "variance_minor": invoice.variance_minor,
                        "surcharge_reason": invoice.surcharge_reason,
                        "submitted_at": invoice.submitted_at,
                        "superseded_at": invoice.superseded_at,
                        "line_items": [
                            {
                                "line_number": line.line_number,
                                "category": line.category,
                                "label": line.label,
                                "amount_minor": line.amount_minor,
                                "reason": line.reason,
                            }
                            for line in lines_by_invoice.get(invoice.id, [])
                        ],
                    },
                )
            )
        for dispute in disputes:
            sources.append(
                _record(
                    "reconciliation_dispute",
                    dispute.id,
                    None,
                    {
                        "reconciliation_id": dispute.reconciliation_id,
                        "invoice_revision_id": dispute.invoice_revision_id,
                        "buyer_id": dispute.buyer_id,
                        "disputed_amount_minor": dispute.disputed_amount_minor,
                        "reason": dispute.reason,
                        "opened_at": dispute.opened_at,
                    },
                )
            )
        for variance_approval in variance_approvals:
            sources.append(
                _record(
                    "reconciliation_variance_approval",
                    variance_approval.id,
                    None,
                    {
                        "reconciliation_id": variance_approval.reconciliation_id,
                        "invoice_revision_id": variance_approval.invoice_revision_id,
                        "buyer_id": variance_approval.buyer_id,
                        "approved_variance_minor": variance_approval.approved_variance_minor,
                        "resolves_dispute_id": variance_approval.resolves_dispute_id,
                        "approved_at": variance_approval.approved_at,
                        "note": variance_approval.note,
                    },
                )
            )
        return sources

    @staticmethod
    def _decision_record(row: DecisionEvidenceSnapshotRow) -> DecisionEvidenceRecord:
        try:
            decoded = cast(object, json.loads(row.canonical_json))
        except (json.JSONDecodeError, TypeError) as exc:
            raise EntityConflictError(
                f"decision evidence {row.id} has invalid canonical JSON"
            ) from exc
        if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
            raise EntityConflictError(
                f"decision evidence {row.id} canonical JSON must be an object"
            )
        body = cast(dict[str, object], decoded)
        expected_envelope: dict[str, object] = {
            "schema_version": row.schema_version,
            "decision_type": row.decision_type,
            "subject_type": row.subject_type,
            "subject_id": str(row.subject_id),
            "source_aggregate_type": row.source_aggregate_type,
            "source_aggregate_id": str(row.source_aggregate_id),
            "decided_at": _utc(row.decided_at).isoformat().replace("+00:00", "Z"),
            "known_as_of": (
                _utc(row.known_as_of).isoformat().replace("+00:00", "Z")
                if row.known_as_of is not None
                else None
            ),
            "actor_id": str(row.actor_id) if row.actor_id is not None else None,
            "correlation_id": (str(row.correlation_id) if row.correlation_id is not None else None),
        }
        for key, value in expected_envelope.items():
            if body.get(key) != value:
                raise EntityConflictError(
                    f"decision evidence {row.id} canonical envelope conflicts at {key}"
                )

        content_obj = body.get("content")
        policies_obj = body.get("policy_versions")
        if not isinstance(content_obj, dict) or not isinstance(policies_obj, dict):
            raise EntityConflictError(
                f"decision evidence {row.id} is missing content or policy versions"
            )
        return DecisionEvidenceRecord(
            snapshot_id=row.id,
            decision_type=row.decision_type,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            source_aggregate_type=row.source_aggregate_type,
            source_aggregate_id=row.source_aggregate_id,
            schema_version=row.schema_version,
            decided_at=row.decided_at,
            known_as_of=row.known_as_of,
            actor_id=row.actor_id,
            correlation_id=row.correlation_id,
            policy_versions=cast(dict[str, object], policies_obj),
            content=cast(dict[str, object], content_obj),
            integrity_digest=row.integrity_digest,
        )
