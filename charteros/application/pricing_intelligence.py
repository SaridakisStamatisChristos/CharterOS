from __future__ import annotations

from datetime import timedelta

from charteros.application.ports.pricing_intelligence import PricingIntelligenceReadRepository
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.time_range import TimeRange
from charteros.pricing_intelligence import PricingDataset, build_pricing_dataset

MAX_PRICING_DATASET_ROWS = 5_000
MAX_PRICING_DATASET_WINDOW = timedelta(days=3_660)


class PricingIntelligenceService:
    def __init__(self, repository: PricingIntelligenceReadRepository) -> None:
        self._repository = repository

    def build_dataset(
        self,
        *,
        source_window: TimeRange,
        limit: int = 1_000,
    ) -> PricingDataset:
        if not 1 <= limit <= MAX_PRICING_DATASET_ROWS:
            raise DomainValidationError(
                f"pricing dataset limit must be between 1 and {MAX_PRICING_DATASET_ROWS}"
            )
        if source_window.duration > MAX_PRICING_DATASET_WINDOW:
            raise DomainValidationError("pricing dataset source window cannot exceed 3660 days")

        evidence, truncated = self._repository.list_historical_pricing(
            source_window=source_window,
            limit=limit,
        )
        if len(evidence) > limit:
            raise DomainValidationError("pricing repository returned more rows than requested")
        return build_pricing_dataset(
            source_window=source_window,
            evidence=evidence,
            truncated=truncated,
        )
