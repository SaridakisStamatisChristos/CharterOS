from __future__ import annotations

from datetime import datetime
from typing import Protocol

from charteros.domain.fx import FxLock, FxLockId, FxRateId, FxRateObservation
from charteros.domain.shared.currency import Currency


class FxRateRepository(Protocol):
    def add(self, rate: FxRateObservation) -> None: ...

    def get(self, rate_id: FxRateId) -> FxRateObservation | None: ...

    def get_for_update(self, rate_id: FxRateId) -> FxRateObservation | None: ...

    def find_successor(self, rate_id: FxRateId) -> FxRateObservation | None: ...

    def latest_for_pair(
        self,
        *,
        source_currency: Currency,
        target_currency: Currency,
        fx_source: str,
        known_as_of: datetime,
    ) -> FxRateObservation | None: ...


class FxLockRepository(Protocol):
    def add(self, lock: FxLock) -> None: ...

    def get(self, lock_id: FxLockId) -> FxLock | None: ...

    def get_for_update(self, lock_id: FxLockId) -> FxLock | None: ...

    def save(self, lock: FxLock, *, expected_version: int) -> None: ...
