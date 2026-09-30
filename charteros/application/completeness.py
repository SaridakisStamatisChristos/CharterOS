from __future__ import annotations

from collections.abc import Sequence

from charteros.application.exceptions import EntityConflictError


class BoundedInputOverflowError(EntityConflictError):
    """A bounded read observed at least one item beyond the requested complete universe."""

    def __init__(
        self,
        *,
        reason: str,
        limit: int,
        observed_count_at_least: int,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if observed_count_at_least <= limit:
            raise ValueError("observed_count_at_least must exceed limit")
        self.reason = reason
        self.limit = limit
        self.observed_count_at_least = observed_count_at_least
        super().__init__(
            f"{reason}; limit={limit}; observed_count_at_least={observed_count_at_least}"
        )


def require_complete_bounded[T](
    items: Sequence[T],
    *,
    limit: int,
    reason: str,
) -> tuple[T, ...]:
    """Return a complete bounded result or fail if the overflow probe found another item."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(items) > limit:
        raise BoundedInputOverflowError(
            reason=reason,
            limit=limit,
            observed_count_at_least=limit + 1,
        )
    return tuple(items)
