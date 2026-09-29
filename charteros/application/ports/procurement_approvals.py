from __future__ import annotations

from typing import Protocol

from charteros.domain.missions import MissionId
from charteros.domain.procurement_approvals import ProcurementApproval, ProcurementApprovalId


class ProcurementApprovalRepository(Protocol):
    def add(self, approval: ProcurementApproval) -> None: ...

    def get(self, approval_id: ProcurementApprovalId) -> ProcurementApproval | None: ...

    def get_for_update(
        self,
        approval_id: ProcurementApprovalId,
    ) -> ProcurementApproval | None: ...

    def get_current_for_mission_for_update(
        self,
        mission_id: MissionId,
    ) -> ProcurementApproval | None: ...

    def save(self, approval: ProcurementApproval, *, expected_version: int) -> None: ...
