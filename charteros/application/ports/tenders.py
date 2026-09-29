from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import RfqId
from charteros.domain.tenders import (
    Tender,
    TenderAdminCorrection,
    TenderId,
    TenderInvitation,
    TenderInvitationId,
)


@dataclass(frozen=True, slots=True)
class TenderAuditEvent:
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    canonical_json: str


class TenderRepository(Protocol):
    def add(self, tender: Tender) -> None: ...

    def get(self, tender_id: TenderId) -> Tender | None: ...

    def get_for_update(self, tender_id: TenderId) -> Tender | None: ...

    def get_for_mission(self, mission_id: MissionId) -> Tender | None: ...

    def save(self, tender: Tender, *, expected_version: int) -> None: ...

    def add_invitation(self, invitation: TenderInvitation) -> None: ...

    def get_invitation(self, invitation_id: TenderInvitationId) -> TenderInvitation | None: ...

    def get_invitation_for_update(
        self, invitation_id: TenderInvitationId
    ) -> TenderInvitation | None: ...

    def find_invitation_for_operator(
        self, tender_id: TenderId, operator_id: OperatorId
    ) -> TenderInvitation | None: ...

    def find_invitation_for_rfq(self, rfq_id: RfqId) -> TenderInvitation | None: ...

    def list_invitations(self, tender_id: TenderId) -> tuple[TenderInvitation, ...]: ...

    def save_invitation(self, invitation: TenderInvitation) -> None: ...

    def add_correction(self, correction: TenderAdminCorrection) -> None: ...

    def list_corrections(self, tender_id: TenderId) -> tuple[TenderAdminCorrection, ...]: ...

    def list_audit_events(self, tender_id: TenderId) -> tuple[TenderAuditEvent, ...]: ...
