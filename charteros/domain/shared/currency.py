from __future__ import annotations

from dataclasses import dataclass

from charteros.domain.shared.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True, order=True)
class Currency:
    """ISO-4217 alpha-code value object.

    This primitive validates the canonical three-letter representation. Membership in the
    evolving ISO registry is intentionally handled as reference data rather than a stale
    hard-coded list in the domain kernel.
    """

    code: str

    def __post_init__(self) -> None:
        if (
            len(self.code) != 3
            or not self.code.isascii()
            or not self.code.isalpha()
            or not self.code.isupper()
        ):
            raise DomainValidationError(
                "currency code must be exactly three uppercase ASCII letters"
            )

    def __str__(self) -> str:
        return self.code
