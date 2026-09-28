from __future__ import annotations

from dataclasses import dataclass

from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import (
    CurrencyMismatchError,
    DomainValidationError,
    MoneyOverflowError,
)

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _checked_int64(value: int) -> int:
    if value < _INT64_MIN or value > _INT64_MAX:
        raise MoneyOverflowError("money amount exceeds signed int64 bounds")
    return value


@dataclass(frozen=True, slots=True)
class Money:
    """Exact monetary amount represented in currency minor units."""

    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise DomainValidationError("amount_minor must be an integer")
        if not isinstance(self.currency, Currency):
            raise DomainValidationError("currency must be a Currency value")
        _checked_int64(self.amount_minor)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"currency mismatch: {self.currency} != {other.currency}"
            )

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(_checked_int64(self.amount_minor + other.amount_minor), self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(_checked_int64(self.amount_minor - other.amount_minor), self.currency)

    def __neg__(self) -> Money:
        return Money(_checked_int64(-self.amount_minor), self.currency)

    def __mul__(self, factor: object) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            return NotImplemented
        return Money(_checked_int64(self.amount_minor * factor), self.currency)

    def __rmul__(self, factor: object) -> Money:
        return self.__mul__(factor)

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount_minor <= other.amount_minor
