from datetime import UTC, datetime, timedelta

import pytest

from charteros.application.recovery import elapsed_seconds, observed_data_loss_seconds


def test_observed_data_loss_uses_expected_minus_restored_and_never_goes_negative() -> None:
    restored = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert (
        observed_data_loss_seconds(
            expected_latest_canonical_at=restored + timedelta(seconds=75),
            restored_latest_canonical_at=restored,
        )
        == 75.0
    )
    assert (
        observed_data_loss_seconds(
            expected_latest_canonical_at=restored,
            restored_latest_canonical_at=restored + timedelta(seconds=10),
        )
        == 0.0
    )


def test_observed_data_loss_is_not_fabricated_without_both_timestamps() -> None:
    now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert (
        observed_data_loss_seconds(
            expected_latest_canonical_at=None,
            restored_latest_canonical_at=now,
        )
        is None
    )
    assert (
        observed_data_loss_seconds(
            expected_latest_canonical_at=now,
            restored_latest_canonical_at=None,
        )
        is None
    )


def test_elapsed_seconds_rejects_impossible_recovery_timeline() -> None:
    started = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert elapsed_seconds(started_at=started, completed_at=started + timedelta(seconds=3)) == 3.0
    with pytest.raises(ValueError, match="cannot precede"):
        elapsed_seconds(started_at=started, completed_at=started - timedelta(seconds=1))
