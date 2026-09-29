from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import AircraftId, AvailabilityRecordId
from charteros.domain.bookings import BookingId, BookingState
from charteros.domain.disruptions import (
    Disruption,
    DisruptionBuyerDecision,
    DisruptionBuyerDecisionId,
    DisruptionBuyerDecisionValue,
    DisruptionCommercialChange,
    DisruptionCommercialChangeId,
    DisruptionCommercialChangeStatus,
    DisruptionId,
    DisruptionProposalId,
    DisruptionProposalStatus,
    DisruptionStatus,
    DisruptionType,
    ReplacementProposal,
)
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.disruptions import (
    DisruptionBuyerDecisionRow,
    DisruptionCommercialChangeRow,
    DisruptionProposalRow,
    DisruptionRow,
)


def _disruption_from_row(row: DisruptionRow) -> Disruption:
    return Disruption(
        DisruptionId(row.id),
        booking_id=BookingId(row.booking_id),
        disruption_type=DisruptionType(row.disruption_type),
        status=DisruptionStatus(row.status),
        detected_at=row.detected_at,
        effective_at=row.effective_at,
        reason=row.reason,
        current_proposal_id=(
            DisruptionProposalId(row.current_proposal_id)
            if row.current_proposal_id is not None
            else None
        ),
        current_commercial_change_id=(
            DisruptionCommercialChangeId(row.current_commercial_change_id)
            if row.current_commercial_change_id is not None
            else None
        ),
        latest_buyer_decision_id=(
            DisruptionBuyerDecisionId(row.latest_buyer_decision_id)
            if row.latest_buyer_decision_id is not None
            else None
        ),
        selected_proposal_id=(
            DisruptionProposalId(row.selected_proposal_id)
            if row.selected_proposal_id is not None
            else None
        ),
        selected_commercial_change_id=(
            DisruptionCommercialChangeId(row.selected_commercial_change_id)
            if row.selected_commercial_change_id is not None
            else None
        ),
        selected_buyer_decision_id=(
            DisruptionBuyerDecisionId(row.selected_buyer_decision_id)
            if row.selected_buyer_decision_id is not None
            else None
        ),
        resolved_at=row.resolved_at,
        resolution_outcome=row.resolution_outcome,
        booking_state_at_resolution=(
            BookingState(row.booking_state_at_resolution)
            if row.booking_state_at_resolution is not None
            else None
        ),
        version=row.version,
    )


def _proposal_from_row(row: DisruptionProposalRow) -> ReplacementProposal:
    window = None
    if row.departure_start is not None and row.departure_end is not None:
        window = TimeRange(row.departure_start, row.departure_end)
    return ReplacementProposal(
        id=DisruptionProposalId(row.id),
        disruption_id=DisruptionId(row.disruption_id),
        revision_number=row.revision_number,
        supersedes_proposal_id=(
            DisruptionProposalId(row.supersedes_proposal_id)
            if row.supersedes_proposal_id is not None
            else None
        ),
        status=DisruptionProposalStatus(row.status),
        proposed_operator_id=OperatorId(row.proposed_operator_id),
        proposed_aircraft_id=AircraftId(row.proposed_aircraft_id),
        proposed_operator_version=row.proposed_operator_version,
        proposed_aircraft_version=row.proposed_aircraft_version,
        availability_record_id=(
            AvailabilityRecordId(row.availability_record_id)
            if row.availability_record_id is not None
            else None
        ),
        availability_recorded_at=row.availability_recorded_at,
        departure_window=window,
        requires_buyer_decision=row.requires_buyer_decision,
        source=row.source,
        source_evidence=row.source_evidence,
        proposed_at=row.proposed_at,
        superseded_at=row.superseded_at,
    )


def _commercial_from_row(row: DisruptionCommercialChangeRow) -> DisruptionCommercialChange:
    currency = Currency(row.currency)
    return DisruptionCommercialChange(
        id=DisruptionCommercialChangeId(row.id),
        disruption_id=DisruptionId(row.disruption_id),
        proposal_id=DisruptionProposalId(row.proposal_id),
        revision_number=row.revision_number,
        supersedes_change_id=(
            DisruptionCommercialChangeId(row.supersedes_change_id)
            if row.supersedes_change_id is not None
            else None
        ),
        status=DisruptionCommercialChangeStatus(row.status),
        original_quote_id=QuoteId(row.original_quote_id),
        currency=currency,
        normalization_version=row.normalization_version,
        original_expected_total=Money(row.original_expected_total_minor, currency),
        original_worst_case_total=Money(row.original_worst_case_total_minor, currency),
        known_adjustment=Money(row.known_adjustment_minor, currency),
        conditional_adjustment=Money(row.conditional_adjustment_minor, currency),
        resulting_expected_total=Money(row.resulting_expected_total_minor, currency),
        resulting_worst_case_total=Money(row.resulting_worst_case_total_minor, currency),
        terms_summary=row.terms_summary,
        created_at=row.created_at,
        superseded_at=row.superseded_at,
    )


def _decision_from_row(row: DisruptionBuyerDecisionRow) -> DisruptionBuyerDecision:
    return DisruptionBuyerDecision(
        id=DisruptionBuyerDecisionId(row.id),
        disruption_id=DisruptionId(row.disruption_id),
        proposal_id=DisruptionProposalId(row.proposal_id),
        commercial_change_id=(
            DisruptionCommercialChangeId(row.commercial_change_id)
            if row.commercial_change_id is not None
            else None
        ),
        buyer_id=OrganizationId(row.buyer_id),
        decision=DisruptionBuyerDecisionValue(row.decision),
        decided_at=row.decided_at,
        note=row.note,
    )


class SqlAlchemyDisruptionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, disruption: Disruption) -> None:
        self._session.add(
            DisruptionRow(
                id=disruption.id.value,
                version=disruption.version,
                booking_id=disruption.booking_id.value,
                disruption_type=disruption.disruption_type.value,
                status=disruption.status.value,
                detected_at=disruption.detected_at,
                effective_at=disruption.effective_at,
                reason=disruption.reason,
                current_proposal_id=(
                    disruption.current_proposal_id.value
                    if disruption.current_proposal_id is not None
                    else None
                ),
                current_commercial_change_id=(
                    disruption.current_commercial_change_id.value
                    if disruption.current_commercial_change_id is not None
                    else None
                ),
                latest_buyer_decision_id=(
                    disruption.latest_buyer_decision_id.value
                    if disruption.latest_buyer_decision_id is not None
                    else None
                ),
                selected_proposal_id=None,
                selected_commercial_change_id=None,
                selected_buyer_decision_id=None,
                resolved_at=None,
                resolution_outcome=None,
                booking_state_at_resolution=None,
            )
        )
        self._flush("disruption conflicts with persisted booking evidence")

    def get(self, disruption_id: DisruptionId) -> Disruption | None:
        row = self._session.get(DisruptionRow, disruption_id.value)
        return _disruption_from_row(row) if row is not None else None

    def get_for_update(self, disruption_id: DisruptionId) -> Disruption | None:
        row = self._session.scalar(
            select(DisruptionRow).where(DisruptionRow.id == disruption_id.value).with_for_update()
        )
        return _disruption_from_row(row) if row is not None else None

    def list_for_booking(self, booking_id: BookingId, *, limit: int) -> tuple[Disruption, ...]:
        rows = self._session.scalars(
            select(DisruptionRow)
            .where(DisruptionRow.booking_id == booking_id.value)
            .order_by(DisruptionRow.detected_at.desc(), DisruptionRow.id.desc())
            .limit(limit)
        ).all()
        return tuple(_disruption_from_row(row) for row in rows)

    def save(self, disruption: Disruption, *, expected_version: int) -> None:
        updated = self._session.scalar(
            update(DisruptionRow)
            .where(
                DisruptionRow.id == disruption.id.value,
                DisruptionRow.version == expected_version,
            )
            .values(
                version=disruption.version,
                status=disruption.status.value,
                current_proposal_id=(
                    disruption.current_proposal_id.value
                    if disruption.current_proposal_id is not None
                    else None
                ),
                current_commercial_change_id=(
                    disruption.current_commercial_change_id.value
                    if disruption.current_commercial_change_id is not None
                    else None
                ),
                latest_buyer_decision_id=(
                    disruption.latest_buyer_decision_id.value
                    if disruption.latest_buyer_decision_id is not None
                    else None
                ),
                selected_proposal_id=(
                    disruption.selected_proposal_id.value
                    if disruption.selected_proposal_id is not None
                    else None
                ),
                selected_commercial_change_id=(
                    disruption.selected_commercial_change_id.value
                    if disruption.selected_commercial_change_id is not None
                    else None
                ),
                selected_buyer_decision_id=(
                    disruption.selected_buyer_decision_id.value
                    if disruption.selected_buyer_decision_id is not None
                    else None
                ),
                resolved_at=disruption.resolved_at,
                resolution_outcome=disruption.resolution_outcome,
                booking_state_at_resolution=(
                    disruption.booking_state_at_resolution.value
                    if disruption.booking_state_at_resolution is not None
                    else None
                ),
            )
            .returning(DisruptionRow.id)
        )
        if updated is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for disruption {disruption.id}"
            )
        self._session.flush()

    def add_proposal(self, proposal: ReplacementProposal) -> None:
        self._session.add(
            DisruptionProposalRow(
                id=proposal.id.value,
                disruption_id=proposal.disruption_id.value,
                revision_number=proposal.revision_number,
                supersedes_proposal_id=(
                    proposal.supersedes_proposal_id.value
                    if proposal.supersedes_proposal_id is not None
                    else None
                ),
                status=proposal.status.value,
                proposed_operator_id=proposal.proposed_operator_id.value,
                proposed_aircraft_id=proposal.proposed_aircraft_id.value,
                proposed_operator_version=proposal.proposed_operator_version,
                proposed_aircraft_version=proposal.proposed_aircraft_version,
                availability_record_id=(
                    proposal.availability_record_id.value
                    if proposal.availability_record_id is not None
                    else None
                ),
                availability_recorded_at=proposal.availability_recorded_at,
                departure_start=(
                    proposal.departure_window.start
                    if proposal.departure_window is not None
                    else None
                ),
                departure_end=(
                    proposal.departure_window.end if proposal.departure_window is not None else None
                ),
                requires_buyer_decision=proposal.requires_buyer_decision,
                source=proposal.source,
                source_evidence=proposal.source_evidence,
                proposed_at=proposal.proposed_at,
                superseded_at=proposal.superseded_at,
            )
        )
        self._flush("disruption proposal conflicts with the current proposal lineage")

    def get_proposal(self, proposal_id: DisruptionProposalId) -> ReplacementProposal | None:
        row = self._session.get(DisruptionProposalRow, proposal_id.value)
        return _proposal_from_row(row) if row is not None else None

    def get_current_proposal_for_update(
        self,
        disruption_id: DisruptionId,
    ) -> ReplacementProposal | None:
        row = self._session.scalar(
            select(DisruptionProposalRow)
            .where(
                DisruptionProposalRow.disruption_id == disruption_id.value,
                DisruptionProposalRow.status == DisruptionProposalStatus.CURRENT.value,
            )
            .with_for_update()
        )
        return _proposal_from_row(row) if row is not None else None

    def list_proposals(
        self,
        disruption_id: DisruptionId,
        *,
        limit: int,
    ) -> tuple[ReplacementProposal, ...]:
        rows = self._session.scalars(
            select(DisruptionProposalRow)
            .where(DisruptionProposalRow.disruption_id == disruption_id.value)
            .order_by(DisruptionProposalRow.revision_number, DisruptionProposalRow.id)
            .limit(limit)
        ).all()
        return tuple(_proposal_from_row(row) for row in rows)

    def supersede_proposal(
        self,
        proposal_id: DisruptionProposalId,
        *,
        superseded_at: datetime,
    ) -> None:
        updated = self._session.scalar(
            update(DisruptionProposalRow)
            .where(
                DisruptionProposalRow.id == proposal_id.value,
                DisruptionProposalRow.status == DisruptionProposalStatus.CURRENT.value,
            )
            .values(
                status=DisruptionProposalStatus.SUPERSEDED.value,
                superseded_at=superseded_at,
            )
            .returning(DisruptionProposalRow.id)
        )
        if updated is None:
            raise EntityConflictError("current disruption proposal changed concurrently")
        self._session.flush()

    def add_commercial_change(self, change: DisruptionCommercialChange) -> None:
        self._session.add(
            DisruptionCommercialChangeRow(
                id=change.id.value,
                disruption_id=change.disruption_id.value,
                proposal_id=change.proposal_id.value,
                revision_number=change.revision_number,
                supersedes_change_id=(
                    change.supersedes_change_id.value
                    if change.supersedes_change_id is not None
                    else None
                ),
                status=change.status.value,
                original_quote_id=change.original_quote_id.value,
                currency=str(change.currency),
                normalization_version=change.normalization_version,
                original_expected_total_minor=change.original_expected_total.amount_minor,
                original_worst_case_total_minor=change.original_worst_case_total.amount_minor,
                known_adjustment_minor=change.known_adjustment.amount_minor,
                conditional_adjustment_minor=change.conditional_adjustment.amount_minor,
                resulting_expected_total_minor=change.resulting_expected_total.amount_minor,
                resulting_worst_case_total_minor=change.resulting_worst_case_total.amount_minor,
                terms_summary=change.terms_summary,
                created_at=change.created_at,
                superseded_at=change.superseded_at,
            )
        )
        self._flush("disruption commercial change conflicts with the current revision")

    def get_commercial_change(
        self,
        change_id: DisruptionCommercialChangeId,
    ) -> DisruptionCommercialChange | None:
        row = self._session.get(DisruptionCommercialChangeRow, change_id.value)
        return _commercial_from_row(row) if row is not None else None

    def get_current_commercial_change_for_update(
        self,
        proposal_id: DisruptionProposalId,
    ) -> DisruptionCommercialChange | None:
        row = self._session.scalar(
            select(DisruptionCommercialChangeRow)
            .where(
                DisruptionCommercialChangeRow.proposal_id == proposal_id.value,
                DisruptionCommercialChangeRow.status
                == DisruptionCommercialChangeStatus.CURRENT.value,
            )
            .with_for_update()
        )
        return _commercial_from_row(row) if row is not None else None

    def supersede_commercial_change(
        self,
        change_id: DisruptionCommercialChangeId,
        *,
        superseded_at: datetime,
    ) -> None:
        updated = self._session.scalar(
            update(DisruptionCommercialChangeRow)
            .where(
                DisruptionCommercialChangeRow.id == change_id.value,
                DisruptionCommercialChangeRow.status
                == DisruptionCommercialChangeStatus.CURRENT.value,
            )
            .values(
                status=DisruptionCommercialChangeStatus.SUPERSEDED.value,
                superseded_at=superseded_at,
            )
            .returning(DisruptionCommercialChangeRow.id)
        )
        if updated is None:
            raise EntityConflictError("current disruption commercial evidence changed concurrently")
        self._session.flush()

    def add_buyer_decision(self, decision: DisruptionBuyerDecision) -> None:
        self._session.add(
            DisruptionBuyerDecisionRow(
                id=decision.id.value,
                disruption_id=decision.disruption_id.value,
                proposal_id=decision.proposal_id.value,
                commercial_change_id=(
                    decision.commercial_change_id.value
                    if decision.commercial_change_id is not None
                    else None
                ),
                evidence_key=decision.evidence_key,
                buyer_id=decision.buyer_id.value,
                decision=decision.decision.value,
                decided_at=decision.decided_at,
                note=decision.note,
            )
        )
        self._flush("buyer decision already exists for this exact disruption evidence")

    def get_buyer_decision(
        self,
        decision_id: DisruptionBuyerDecisionId,
    ) -> DisruptionBuyerDecision | None:
        row = self._session.get(DisruptionBuyerDecisionRow, decision_id.value)
        return _decision_from_row(row) if row is not None else None

    def _flush(self, message: str) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(message) from exc
