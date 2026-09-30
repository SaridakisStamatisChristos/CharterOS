from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class DatabaseFailureKind(StrEnum):
    """Stable transaction-failure taxonomy used by retry and API policy."""

    SAFE_TRANSIENT = "safe_transient_failure"
    BUSINESS_CONFLICT = "business_conflict"
    INTEGRITY_VIOLATION = "integrity_violation"
    AMBIGUOUS_COMMIT = "ambiguous_commit_outcome"
    PERMANENT_INFRASTRUCTURE = "permanent_infrastructure_failure"


class DatabaseFailurePhase(StrEnum):
    EXECUTION = "execution"
    COMMIT = "commit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DatabaseFailure:
    kind: DatabaseFailureKind
    sqlstate: str | None
    retryable: bool


_SAFE_TRANSIENT_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available / lock timeout
        "57014",  # query_canceled (including statement timeout)
    }
)
_CONNECTION_SQLSTATES = frozenset(
    {
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now / crash recovery
    }
)


class DatabaseTransactionError(RuntimeError):
    """Sanitized database failure surfaced after bounded transaction handling."""

    def __init__(self, failure: DatabaseFailure, *, attempts: int) -> None:
        super().__init__(failure.kind.value)
        self.failure = failure
        self.attempts = attempts


def classify_database_failure(
    exc: SQLAlchemyError,
    *,
    phase: DatabaseFailurePhase,
) -> DatabaseFailure:
    """Classify a SQLAlchemy failure without assuming that every DB error is retryable."""

    sqlstate = _extract_sqlstate(exc)

    if isinstance(exc, IntegrityError) or (sqlstate is not None and sqlstate.startswith("23")):
        return DatabaseFailure(
            kind=DatabaseFailureKind.INTEGRITY_VIOLATION,
            sqlstate=sqlstate,
            retryable=False,
        )

    # QueuePool checkout exhaustion is deliberately not retried in-process. Immediate retry would
    # amplify saturation; the request fails in bounded time and upstream/client policy may retry.
    if isinstance(exc, SQLAlchemyTimeoutError):
        return DatabaseFailure(
            kind=DatabaseFailureKind.PERMANENT_INFRASTRUCTURE,
            sqlstate=sqlstate,
            retryable=False,
        )

    if sqlstate in _SAFE_TRANSIENT_SQLSTATES:
        return DatabaseFailure(
            kind=DatabaseFailureKind.SAFE_TRANSIENT,
            sqlstate=sqlstate,
            retryable=True,
        )

    connection_failure = bool(
        (sqlstate is not None and sqlstate.startswith("08"))
        or sqlstate in _CONNECTION_SQLSTATES
        or (isinstance(exc, DBAPIError) and exc.connection_invalidated)
    )
    if connection_failure:
        if phase is DatabaseFailurePhase.COMMIT:
            return DatabaseFailure(
                kind=DatabaseFailureKind.AMBIGUOUS_COMMIT,
                sqlstate=sqlstate,
                retryable=False,
            )
        return DatabaseFailure(
            kind=DatabaseFailureKind.SAFE_TRANSIENT,
            sqlstate=sqlstate,
            retryable=phase is DatabaseFailurePhase.EXECUTION,
        )

    return DatabaseFailure(
        kind=DatabaseFailureKind.PERMANENT_INFRASTRUCTURE,
        sqlstate=sqlstate,
        retryable=False,
    )


def _extract_sqlstate(exc: SQLAlchemyError) -> str | None:
    origin = exc.orig if isinstance(exc, DBAPIError) else getattr(exc, "orig", None)
    for candidate in (origin, getattr(origin, "__cause__", None)):
        if candidate is None:
            continue
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(value, str) and len(value) == 5:
            return value
    return None
