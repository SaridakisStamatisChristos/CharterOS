class DomainError(Exception):
    """Base class for domain-layer failures."""


class DomainValidationError(DomainError):
    """Raised when a value violates a domain invariant."""


class CurrencyMismatchError(DomainError):
    """Raised when arithmetic or comparison mixes currencies."""


class MoneyOverflowError(DomainError):
    """Raised when a money operation would exceed signed int64 bounds."""


class OptimisticConcurrencyError(DomainError):
    """Raised when an aggregate version differs from the caller expectation."""
