from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from charteros.application.completeness import (
    BoundedInputOverflowError,
    require_complete_bounded,
)
from charteros.application.graph_queries import (
    EmptyLegCandidate,
    GraphQueryReadRepository,
    GraphQueryService,
)
from charteros.application.repositioning import (
    MAX_QUOTED_FUTURE_LEGS,
    MAX_STRUCTURAL_EMPTY_LEGS,
    _optimizer_universe_overflow,
)
from charteros.repositioning import POLICY_VERSION

BASE = datetime(2026, 9, 30, 8, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _structural(seed: int) -> EmptyLegCandidate:
    return EmptyLegCandidate(
        aircraft_id=_id(1 + seed),
        operator_id=_id(10_000 + seed),
        previous_booking_id=_id(20_000 + seed),
        previous_mission_id=_id(30_000 + seed),
        next_booking_id=_id(40_000 + seed),
        next_mission_id=_id(50_000 + seed),
        from_airport_id=_id(60_000 + seed),
        from_icao="AAAA",
        to_airport_id=_id(70_000 + seed),
        to_icao="BBBB",
        window_start=BASE + timedelta(hours=seed),
        window_end=BASE + timedelta(hours=seed + 1),
        gap_minutes=60,
    )


class _StructuralProbeRepository:
    def __init__(self, count: int) -> None:
        self.items = tuple(_structural(seed) for seed in range(1, count + 1))
        self.requested_limits: list[int] = []

    def empty_leg_candidates(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        limit: int,
        operator_id: UUID | None = None,
    ) -> tuple[EmptyLegCandidate, ...]:
        del window_start, window_end, operator_id
        self.requested_limits.append(limit)
        return self.items[:limit]


@pytest.mark.parametrize(
    ("count", "fails"),
    [
        (99, False),
        (100, False),
        (101, True),
    ],
)
def test_structural_optimizer_boundary_is_complete_or_fails_closed(
    count: int,
    fails: bool,
) -> None:
    repository = _StructuralProbeRepository(count)
    service = GraphQueryService(cast(GraphQueryReadRepository, repository))

    if fails:
        with pytest.raises(BoundedInputOverflowError) as captured:
            service.empty_leg_candidates(
                window_start=BASE,
                window_end=BASE + timedelta(days=30),
                limit=MAX_STRUCTURAL_EMPTY_LEGS,
                require_complete=True,
            )
        assert captured.value.reason == (
            "structural_candidate_universe_exceeds_requested_capacity"
        )
        assert captured.value.limit == MAX_STRUCTURAL_EMPTY_LEGS
        assert captured.value.observed_count_at_least == 101
    else:
        items = service.empty_leg_candidates(
            window_start=BASE,
            window_end=BASE + timedelta(days=30),
            limit=MAX_STRUCTURAL_EMPTY_LEGS,
            require_complete=True,
        )
        assert len(items) == count

    assert repository.requested_limits == [MAX_STRUCTURAL_EMPTY_LEGS + 1]


def test_bounded_graph_browsing_still_returns_first_100_without_optimizer_semantics() -> None:
    repository = _StructuralProbeRepository(101)
    service = GraphQueryService(cast(GraphQueryReadRepository, repository))

    items = service.empty_leg_candidates(
        window_start=BASE,
        window_end=BASE + timedelta(days=30),
        limit=MAX_STRUCTURAL_EMPTY_LEGS,
    )

    assert len(items) == MAX_STRUCTURAL_EMPTY_LEGS
    assert repository.requested_limits == [MAX_STRUCTURAL_EMPTY_LEGS]


@pytest.mark.parametrize(
    ("count", "fails"),
    [
        (1999, False),
        (2000, False),
        (2001, True),
    ],
)
def test_quoted_opportunity_boundary_is_complete_or_fails_closed(
    count: int,
    fails: bool,
) -> None:
    items = tuple(range(count))

    if fails:
        with pytest.raises(BoundedInputOverflowError) as captured:
            require_complete_bounded(
                items,
                limit=MAX_QUOTED_FUTURE_LEGS,
                reason="quoted_future_leg_universe_exceeds_requested_capacity",
            )
        assert captured.value.limit == MAX_QUOTED_FUTURE_LEGS
        assert captured.value.observed_count_at_least == 2001
    else:
        assert require_complete_bounded(
            items,
            limit=MAX_QUOTED_FUTURE_LEGS,
            reason="quoted_future_leg_universe_exceeds_requested_capacity",
        ) == items


def test_optimizer_overflow_error_is_deterministic_and_policy_versioned() -> None:
    cause = BoundedInputOverflowError(
        reason="structural_candidate_universe_exceeds_requested_capacity",
        limit=MAX_STRUCTURAL_EMPTY_LEGS,
        observed_count_at_least=MAX_STRUCTURAL_EMPTY_LEGS + 1,
    )

    first = _optimizer_universe_overflow(
        cause,
        universe="structural",
        requested_limit=MAX_STRUCTURAL_EMPTY_LEGS,
        validated_limit=MAX_STRUCTURAL_EMPTY_LEGS,
    )
    second = _optimizer_universe_overflow(
        cause,
        universe="structural",
        requested_limit=MAX_STRUCTURAL_EMPTY_LEGS,
        validated_limit=MAX_STRUCTURAL_EMPTY_LEGS,
    )

    assert str(first) == str(second)
    assert f"policy_version={POLICY_VERSION}" in str(first)
    assert "validated_structural_limit=100" in str(first)
    assert "observed_count_at_least=101" in str(first)
    assert "reason=structural_candidate_universe_exceeds_validated_capacity" in str(first)
