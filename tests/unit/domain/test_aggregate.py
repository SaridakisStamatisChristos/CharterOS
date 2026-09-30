from datetime import UTC, datetime

import pytest

from charteros.domain.shared import (
    AggregateRoot,
    OptimisticConcurrencyError,
    TypedId,
)
from charteros.domain.shared.exceptions import DomainValidationError


class SampleAggregateId(TypedId):
    __slots__ = ()


class SampleAggregate(AggregateRoot[SampleAggregateId]):
    aggregate_type = "test_aggregate"

    def change_name(
        self,
        name: str,
        *,
        recorded_at: datetime,
        occurred_at: datetime | None = None,
    ) -> None:
        self._record_event(
            "NAME_CHANGED",
            {"name": name},
            recorded_at=recorded_at,
            occurred_at=occurred_at,
        )


RECORDED_AT = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
OCCURRED_AT = datetime(2026, 9, 29, 18, 30, tzinfo=UTC)


def test_aggregate_version_advances_with_recorded_events() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new(), version=4)

    aggregate.change_name("CharterOS", recorded_at=RECORDED_AT)

    assert aggregate.version == 5
    pending_before = aggregate.pending_events
    assert len(pending_before) == 1
    assert pending_before[0].aggregate_version == 5

    collected = aggregate.collect_events()
    pending_after = aggregate.pending_events
    assert len(collected) == 1
    assert len(pending_after) == 0
    assert aggregate.version == 5


def test_explicit_recorded_at_is_preserved_exactly() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new())

    aggregate.change_name("CharterOS", recorded_at=RECORDED_AT)

    assert aggregate.pending_events[0].recorded_at == RECORDED_AT


def test_omitted_occurrence_defaults_to_recorded_at() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new())

    aggregate.change_name("CharterOS", recorded_at=RECORDED_AT)

    event = aggregate.pending_events[0]
    assert event.occurred_at == event.recorded_at == RECORDED_AT


def test_historical_occurrence_and_recorded_time_are_both_preserved() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new())

    aggregate.change_name(
        "CharterOS",
        recorded_at=RECORDED_AT,
        occurred_at=OCCURRED_AT,
    )

    event = aggregate.pending_events[0]
    assert event.occurred_at == OCCURRED_AT
    assert event.recorded_at == RECORDED_AT


def test_future_occurrence_fails_closed() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new())
    future = datetime(2026, 9, 30, 10, 0, 1, tzinfo=UTC)

    with pytest.raises(DomainValidationError, match="recorded_at cannot precede occurred_at"):
        aggregate.change_name(
            "CharterOS",
            recorded_at=RECORDED_AT,
            occurred_at=future,
        )


@pytest.mark.parametrize(
    ("recorded_at", "occurred_at", "field_name"),
    [
        (datetime(2026, 9, 30, 10, 0), None, "recorded_at"),
        (RECORDED_AT, datetime(2026, 9, 29, 18, 30), "occurred_at"),
    ],
)
def test_naive_event_timestamps_are_rejected(
    recorded_at: datetime,
    occurred_at: datetime | None,
    field_name: str,
) -> None:
    aggregate = SampleAggregate(SampleAggregateId.new())

    with pytest.raises(DomainValidationError, match=rf"{field_name} must be timezone-aware"):
        aggregate.change_name(
            "CharterOS",
            recorded_at=recorded_at,
            occurred_at=occurred_at,
        )


def test_event_timestamps_are_independent_of_system_wall_clock() -> None:
    first = SampleAggregate(SampleAggregateId.new())
    second = SampleAggregate(SampleAggregateId.new())

    first.change_name(
        "CharterOS",
        recorded_at=RECORDED_AT,
        occurred_at=OCCURRED_AT,
    )
    second.change_name(
        "CharterOS",
        recorded_at=RECORDED_AT,
        occurred_at=OCCURRED_AT,
    )

    first_event = first.pending_events[0]
    second_event = second.pending_events[0]
    assert (first_event.occurred_at, first_event.recorded_at) == (
        second_event.occurred_at,
        second_event.recorded_at,
    )


def test_aggregate_version_guard_detects_stale_writes() -> None:
    aggregate = SampleAggregate(SampleAggregateId.new(), version=7)

    aggregate.assert_version(7)
    with pytest.raises(OptimisticConcurrencyError):
        aggregate.assert_version(6)
