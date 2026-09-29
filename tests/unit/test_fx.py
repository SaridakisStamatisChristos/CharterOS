from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from charteros.domain.fx import (
    FX_LOCK_TTL_SECONDS,
    FxLock,
    FxLockedQuote,
    FxRateObservation,
    canonical_rate_text,
    convert_money,
)
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import ProcurementApprovalId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError, MoneyOverflowError
from charteros.domain.shared.money import Money

NOW = datetime(2026, 9, 29, 18, tzinfo=UTC)


def _rate(
    *,
    source: str = "USD",
    target: str = "EUR",
    rate: str = "0.8421",
    source_exponent: int = 2,
    target_exponent: int = 2,
) -> FxRateObservation:
    return FxRateObservation.record(
        source_currency=Currency(source),
        target_currency=Currency(target),
        rate_text=rate,
        source_minor_exponent=source_exponent,
        target_minor_exponent=target_exponent,
        fx_source="test-feed",
        fx_source_version="feed-v1",
        fx_timestamp=NOW - timedelta(seconds=1),
        recorded_at=NOW,
    )


def test_usd_to_eur_exact_example_matches_buyer_evidence() -> None:
    conversion = convert_money(
        amount=Money(8_200_000, Currency("USD")),
        rate=_rate(),
        base_currency=Currency("EUR"),
    )

    assert conversion.converted == Money(6_905_220, Currency("EUR"))
    assert conversion.rate_text == "0.8421"


@pytest.mark.parametrize(
    ("source_exponent", "target_exponent", "amount_minor", "rate_text", "expected_minor"),
    [
        (0, 2, 100, "0.00625", 62),
        (2, 3, 12345, "0.5", 61725),
        (3, 2, 12345, "2", 2469),
        (2, 4, 12345, "1.25", 1543125),
    ],
)
def test_conversion_handles_explicit_minor_unit_exponents(
    source_exponent: int,
    target_exponent: int,
    amount_minor: int,
    rate_text: str,
    expected_minor: int,
) -> None:
    conversion = convert_money(
        amount=Money(amount_minor, Currency("AAA")),
        rate=_rate(
            source="AAA",
            target="BBB",
            rate=rate_text,
            source_exponent=source_exponent,
            target_exponent=target_exponent,
        ),
        base_currency=Currency("BBB"),
    )

    assert conversion.converted.amount_minor == expected_minor


def test_half_even_rounding_is_centralized_and_deterministic() -> None:
    rate = _rate(
        source="AAA",
        target="BBB",
        rate="0.625",
        source_exponent=0,
        target_exponent=0,
    )

    even = convert_money(
        amount=Money(100, Currency("AAA")),
        rate=rate,
        base_currency=Currency("BBB"),
    )
    odd = convert_money(
        amount=Money(102, Currency("AAA")),
        rate=rate,
        base_currency=Currency("BBB"),
    )

    assert even.converted.amount_minor == 62
    assert odd.converted.amount_minor == 64


def test_rate_must_be_plain_positive_decimal_string() -> None:
    assert canonical_rate_text("0.842100") == "0.8421"
    with pytest.raises(DomainValidationError):
        canonical_rate_text("8.421e-1")
    with pytest.raises(DomainValidationError):
        canonical_rate_text("0")
    with pytest.raises(DomainValidationError):
        canonical_rate_text("-1")


def test_conversion_preserves_money_int64_boundary() -> None:
    with pytest.raises(MoneyOverflowError):
        convert_money(
            amount=Money(2**62, Currency("AAA")),
            rate=_rate(source="AAA", target="BBB", rate="4"),
            base_currency=Currency("BBB"),
        )


@given(st.integers(min_value=-(10**12), max_value=10**12))
@pytest.mark.property
def test_exact_unit_rate_with_equal_exponents_preserves_minor_units(amount_minor: int) -> None:
    conversion = convert_money(
        amount=Money(amount_minor, Currency("AAA")),
        rate=_rate(source="AAA", target="BBB", rate="1"),
        base_currency=Currency("BBB"),
    )

    assert conversion.converted.amount_minor == amount_minor


def test_correction_is_new_immutable_revision() -> None:
    original = _rate()
    correction = FxRateObservation.correction(
        previous=original,
        rate_text="0.8423",
        fx_source_version="feed-v2",
        recorded_at=NOW + timedelta(seconds=1),
    )

    assert original.rate_text == "0.8421"
    assert original.revision_number == 1
    assert correction.rate_text == "0.8423"
    assert correction.revision_number == 2
    assert correction.supersedes_rate_id == original.id
    assert correction.id != original.id


def _lock() -> FxLock:
    rate = _rate()
    expected = convert_money(
        amount=Money(8_200_000, Currency("USD")),
        rate=rate,
        base_currency=Currency("EUR"),
    )
    worst = convert_money(
        amount=Money(8_300_000, Currency("USD")),
        rate=rate,
        base_currency=Currency("EUR"),
    )
    entry = FxLockedQuote(
        quote_id=QuoteId.new(),
        quote_revision_number=1,
        original_expected=expected.original,
        original_worst_case=worst.original,
        converted_expected=expected.converted,
        converted_worst_case=worst.converted,
        rate_id=rate.id,
        rate_text=rate.rate_text,
        fx_source=rate.fx_source,
        fx_source_version=rate.fx_source_version,
        fx_timestamp=rate.fx_timestamp,
        rate_recorded_at=rate.recorded_at,
        source_minor_exponent=rate.source_minor_exponent,
        target_minor_exponent=rate.target_minor_exponent,
        global_rank=1,
        global_score_method="fx_global_currency_cohort_minmax_v1",
        global_score_total_basis_points=10_000,
    )
    return FxLock.create(
        buyer_id=OrganizationId.new(),
        mission_id=MissionId.new(),
        base_currency=Currency("EUR"),
        fx_source="test-feed",
        locked_at=NOW,
        entries=(entry,),
        integrity_digest="a" * 64,
    )


def test_lock_is_executable_before_but_not_at_expiry_boundary() -> None:
    lock = _lock()
    entry = lock.entries[0]

    assert (lock.expires_at - lock.locked_at).total_seconds() == FX_LOCK_TTL_SECONDS
    assert (
        lock.assert_usable(
            buyer_id=lock.buyer_id,
            mission_id=lock.mission_id,
            quote_id=entry.quote_id,
            quote_revision_number=entry.quote_revision_number,
            at=lock.expires_at - timedelta(microseconds=1),
        )
        == entry
    )

    with pytest.raises(DomainValidationError, match="expired"):
        lock.assert_usable(
            buyer_id=lock.buyer_id,
            mission_id=lock.mission_id,
            quote_id=entry.quote_id,
            quote_revision_number=entry.quote_revision_number,
            at=lock.expires_at,
        )


def test_consumed_lock_cannot_be_reused() -> None:
    lock = _lock()
    lock.consume(
        approval_id=ProcurementApprovalId.new(),
        consumed_at=lock.locked_at + timedelta(seconds=1),
    )

    entry = lock.entries[0]
    with pytest.raises(DomainValidationError, match="already been consumed"):
        lock.assert_usable(
            buyer_id=lock.buyer_id,
            mission_id=lock.mission_id,
            quote_id=entry.quote_id,
            quote_revision_number=entry.quote_revision_number,
            at=lock.locked_at + timedelta(seconds=2),
        )
