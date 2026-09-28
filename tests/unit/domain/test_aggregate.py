from datetime import UTC, datetime

import pytest

from charteros.domain.shared import (
    AggregateRoot,
    OptimisticConcurrencyError,
    TypedId,
)


class SampleAggregateId(TypedId):
    __slots__ = ()


class SampleAggregate(AggregateRoot[SampleAggregateId]):
    aggregate_type = "test_aggregate"

    def change_name(self, name: str) -> None:
        self._record_event(
            "NAME_CHANGED",
            {"name": name},
            occurred_at=datetime(2020, 1, 1, 12, 0, tzinfo=UTC),
        )


def test_aggregate_version_advances_with_recorded_events() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new(), version=4)

    aggregate.change_name("CharterOS")

    assert aggregate.version == 5
    pending_before = aggregate.pending_events
    assert len(pending_before) == 1
    assert pending_before[0].aggregate_version == 5

    collected = aggregate.collect_events()
    pending_after = aggregate.pending_events
    assert len(collected) == 1
    assert len(pending_after) == 0
    assert aggregate.version == 5


def test_aggregate_version_guard_detects_stale_writes() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new(), version=7)

    aggregate.assert_version(7)
    with pytest.raises(OptimisticConcurrencyError):
        aggregate.assert_version(6)
