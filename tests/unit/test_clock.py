from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from charteros.domain.shared.exceptions import DomainValidationError
from charteros.shared.clock import FrozenClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    observed = SystemClock().now()

    assert observed.tzinfo is UTC
    assert observed.utcoffset() == timedelta(0)


def test_frozen_clock_normalizes_explicit_offset_to_utc() -> None:
    source = datetime(2026, 9, 30, 12, 30, tzinfo=timezone(timedelta(hours=3)))
    clock = FrozenClock(source)

    assert clock.now() == datetime(2026, 9, 30, 9, 30, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_frozen_clock_rejects_naive_time() -> None:
    with pytest.raises(DomainValidationError, match="instant must be timezone-aware"):
        FrozenClock(datetime(2026, 9, 30, 12, 30))


def test_api_routes_cannot_read_system_wall_clock_directly() -> None:
    offenders = [
        str(path)
        for path in sorted(Path("apps/api/routes").glob("*.py"))
        if "datetime.now(" in path.read_text(encoding="utf-8")
        or "datetime.utcnow(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
