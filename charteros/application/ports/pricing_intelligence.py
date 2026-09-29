from __future__ import annotations

from typing import Protocol

from charteros.domain.shared.time_range import TimeRange
from charteros.pricing_intelligence import HistoricalPricingEvidence


class PricingIntelligenceReadRepository(Protocol):
    def list_historical_pricing(
        self,
        *,
        source_window: TimeRange,
        limit: int,
    ) -> tuple[tuple[HistoricalPricingEvidence, ...], bool]: ...
