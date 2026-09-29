from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.bookings import BookingId
from charteros.domain.disruptions import (
    Disruption,
    DisruptionBuyerDecision,
    DisruptionBuyerDecisionId,
    DisruptionCommercialChange,
    DisruptionCommercialChangeId,
    DisruptionId,
    DisruptionProposalId,
    ReplacementProposal,
)


class DisruptionRepository(Protocol):
    def add(self, disruption: Disruption) -> None: ...

    def get(self, disruption_id: DisruptionId) -> Disruption | None: ...

    def get_for_update(self, disruption_id: DisruptionId) -> Disruption | None: ...

    def list_for_booking(self, booking_id: BookingId, *, limit: int) -> tuple[Disruption, ...]: ...

    def save(self, disruption: Disruption, *, expected_version: int) -> None: ...

    def add_proposal(self, proposal: ReplacementProposal) -> None: ...

    def get_proposal(self, proposal_id: DisruptionProposalId) -> ReplacementProposal | None: ...

    def get_current_proposal_for_update(
        self,
        disruption_id: DisruptionId,
    ) -> ReplacementProposal | None: ...

    def list_proposals(
        self,
        disruption_id: DisruptionId,
        *,
        limit: int,
    ) -> tuple[ReplacementProposal, ...]: ...

    def supersede_proposal(
        self,
        proposal_id: DisruptionProposalId,
        *,
        superseded_at: datetime,
    ) -> None: ...

    def add_commercial_change(self, change: DisruptionCommercialChange) -> None: ...

    def get_commercial_change(
        self,
        change_id: DisruptionCommercialChangeId,
    ) -> DisruptionCommercialChange | None: ...

    def get_current_commercial_change_for_update(
        self,
        proposal_id: DisruptionProposalId,
    ) -> DisruptionCommercialChange | None: ...

    def supersede_commercial_change(
        self,
        change_id: DisruptionCommercialChangeId,
        *,
        superseded_at: datetime,
    ) -> None: ...

    def add_buyer_decision(self, decision: DisruptionBuyerDecision) -> None: ...

    def get_buyer_decision(
        self,
        decision_id: DisruptionBuyerDecisionId,
    ) -> DisruptionBuyerDecision | None: ...
