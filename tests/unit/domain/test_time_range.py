from datetime import UTC, datetime, timedelta, timezone

import pytest

from charteros.domain.shared import DomainValidationError, TimeRange


def test_time_range_canonicalizes_to_utc_and_is_half_open() -> None:
    eet = timezone(timedelta(hours=2))
    window = TimeRange(
        datetime(2026, 9, 28, 14, 0, tzinfo=eet),
        datetime(2026, 9, 28, 16, 0, tzinfo=eet),
    )

    assert window.start == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert window.contains(datetime(2026, 9, 28, 12, 0, tzinfo=UTC))
    assert not window.contains(datetime(2026, 9, 28, 14, 0, tzinfo=UTC))


def test_touching_ranges_do_not_overlap() -> None:
    first = TimeRange(
        datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 28, 13, 0, tzinfo=UTC),
    )
    second = TimeRange(
        datetime(2026, 9, 28, 13, 0, tzinfo=UTC),
        datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
    )

    assert not first.overlaps(second)


def test_time_range_rejects_naive_or_non_positive_intervals() -> None:
    with pytest.raises(DomainValidationError):
        TimeRange(datetime(2026, 1, 1), datetime(2026, 1, 2))

    instant = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DomainValidationError):
        TimeRange(instant, instant)
