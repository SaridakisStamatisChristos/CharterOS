from __future__ import annotations

from typing import Protocol

from charteros.domain.missions import Mission, MissionId


class MissionRepository(Protocol):
    def add(self, mission: Mission) -> None: ...

    def get(self, mission_id: MissionId) -> Mission | None: ...

    def get_for_update(self, mission_id: MissionId) -> Mission | None: ...

    def save(self, mission: Mission, *, expected_version: int) -> None: ...
