import pytest

from charteros.domain.shared import (
    Currency,
    CurrencyMismatchError,
    DomainValidationError,
    Money,
    MoneyOverflowError,
)

EUR = Currency("EUR")
USD = Currency("USD")


def test_money_arithmetic_is_exact_in_minor_units() -> None:
    assert Money(7_400_000, EUR) + Money(125_000, EUR) == Money(7_525_000, EUR)
    assert Money(7_525_000, EUR) - Money(125_000, EUR) == Money(7_400_000, EUR)
    assert 3 * Money(125_000, EUR) == Money(375_000, EUR)


def test_money_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money(100, EUR) + Money(100, USD)

    with pytest.raises(CurrencyMismatchError):
        _ = Money(100, EUR) < Money(100, USD)


def test_money_enforces_signed_int64_bounds() -> None:
    with pytest.raises(MoneyOverflowError):
        _ = Money(2**63 - 1, EUR) + Money(1, EUR)


def test_money_rejects_boolean_minor_units() -> None:
    with pytest.raises(DomainValidationError):
        Money(True, EUR)  # type: ignore[arg-type]


def test_currency_requires_canonical_alpha_code() -> None:
    with pytest.raises(DomainValidationError):
        Currency("eur")
