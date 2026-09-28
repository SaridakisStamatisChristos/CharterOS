from __future__ import annotations

from typing import Protocol

from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import Rfq, RfqId


class RfqRepository(Protocol):
    def add(self, rfq: Rfq) -> None: ...

    def get(self, rfq_id: RfqId) -> Rfq | None: ...

    def get_for_update(self, rfq_id: RfqId) -> Rfq | None: ...

    def find_for_mission_operator(
        self,
        mission_id: MissionId,
        operator_id: OperatorId,
    ) -> Rfq | None: ...

    def list_for_mission(self, mission_id: MissionId) -> tuple[Rfq, ...]: ...

    def save(self, rfq: Rfq, *, expected_version: int) -> None: ...
