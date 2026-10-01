from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from charteros.infrastructure.db.failures import (
    DatabaseFailureKind,
    DatabaseFailurePhase,
    DatabaseTransactionError,
    classify_database_failure,
)
from charteros.observability import get_operational_metrics


@dataclass(frozen=True, slots=True)
class TransactionRetryPolicy:
    """Bounded outer-transaction retry policy.

    The default is one retry after the original attempt. Callers must only use this boundary when
    replay is proven safe, normally through the same transactional idempotency record.
    """

    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")


DEFAULT_RETRY_POLICY = TransactionRetryPolicy()


def run_transaction[T](
    session: Session,
    action: Callable[[], T],
    *,
    retry_policy: TransactionRetryPolicy = DEFAULT_RETRY_POLICY,
    reconcile_ambiguous: Callable[[], T | None] | None = None,
) -> T:
    """Run one explicit transaction with bounded retry and commit-outcome reconciliation.

    SQL errors during the action are known to occur before commit. Only explicitly classified
    rollback-safe failures may retry. A connection failure raised by commit is different: the
    server may already have committed. Such an outcome is never replayed blindly; callers may
    reconcile it by reading the canonical idempotency record in a fresh transaction.
    """

    metrics = get_operational_metrics()
    started = perf_counter()

    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            session.begin()
            result = action()
            # Surface deferred ORM work before the commit phase whenever possible.
            session.flush()
        except SQLAlchemyError as exc:
            failure = classify_database_failure(exc, phase=DatabaseFailurePhase.EXECUTION)
            _record_decision_snapshot_failure(session)
            _reset_failed_session(session, exc)
            _record_failure_metrics(failure, retrying=failure.retryable and attempt < retry_policy.max_attempts)
            if failure.retryable and attempt < retry_policy.max_attempts:
                continue
            metrics.db_transaction(
                duration_seconds=perf_counter() - started,
                outcome="failure",
                failure_kind=failure.kind.value,
            )
            raise DatabaseTransactionError(failure, attempts=attempt) from exc
        except BaseException:
            _record_decision_snapshot_failure(session)
            _rollback_quietly(session)
            metrics.db_transaction(
                duration_seconds=perf_counter() - started,
                outcome="application_error",
            )
            raise

        try:
            session.commit()
        except SQLAlchemyError as exc:
            failure = classify_database_failure(exc, phase=DatabaseFailurePhase.COMMIT)
            pending_decision_snapshot = bool(
                session.info.pop("charteros_decision_snapshot_pending", False)
            )
            _reset_failed_session(session, exc)

            if failure.kind is DatabaseFailureKind.AMBIGUOUS_COMMIT:
                recovered = _try_reconcile(session, reconcile_ambiguous)
                if recovered is not None:
                    metrics.ambiguous_commit_recovered()
                    metrics.db_transaction(
                        duration_seconds=perf_counter() - started,
                        outcome="success",
                    )
                    return recovered
                if pending_decision_snapshot:
                    metrics.decision_snapshot_write_failure()
                _record_failure_metrics(failure, retrying=False)
                metrics.db_transaction(
                    duration_seconds=perf_counter() - started,
                    outcome="failure",
                    failure_kind=failure.kind.value,
                )
                raise DatabaseTransactionError(failure, attempts=attempt) from exc

            if pending_decision_snapshot:
                metrics.decision_snapshot_write_failure()
            _record_failure_metrics(
                failure,
                retrying=failure.retryable and attempt < retry_policy.max_attempts,
            )
            if failure.retryable and attempt < retry_policy.max_attempts:
                continue
            metrics.db_transaction(
                duration_seconds=perf_counter() - started,
                outcome="failure",
                failure_kind=failure.kind.value,
            )
            raise DatabaseTransactionError(failure, attempts=attempt) from exc

        session.info.pop("charteros_decision_snapshot_pending", None)
        metrics.db_transaction(
            duration_seconds=perf_counter() - started,
            outcome="success",
        )
        return result

    raise RuntimeError("transaction retry loop exhausted unexpectedly")


def _record_decision_snapshot_failure(session: Session) -> None:
    if session.info.pop("charteros_decision_snapshot_pending", False):
        get_operational_metrics().decision_snapshot_write_failure()


def _record_failure_metrics(failure: object, *, retrying: bool) -> None:
    metrics = get_operational_metrics()
    kind = getattr(failure, "kind", None)
    sqlstate = getattr(failure, "sqlstate", None)
    reason = getattr(kind, "value", "other")
    if retrying:
        metrics.db_retry(str(reason))
    if sqlstate == "40P01":
        metrics.db_deadlock()
    if sqlstate in {"55P03", "57014"}:
        metrics.db_lock_wait_failure()
    if (
        (isinstance(sqlstate, str) and sqlstate.startswith("08"))
        or sqlstate in {"57P01", "57P02", "57P03"}
        or kind is DatabaseFailureKind.AMBIGUOUS_COMMIT
    ):
        metrics.db_connectivity_failure()


def _try_reconcile[T](
    session: Session,
    reconcile: Callable[[], T | None] | None,
) -> T | None:
    if reconcile is None:
        return None
    try:
        session.begin()
        recovered = reconcile()
        session.commit()
        return recovered
    except SQLAlchemyError as exc:
        _reset_failed_session(session, exc)
        return None
    except BaseException:
        _rollback_quietly(session)
        raise


def _reset_failed_session(session: Session, exc: SQLAlchemyError) -> None:
    try:
        if isinstance(exc, DBAPIError) and exc.connection_invalidated:
            session.invalidate()
        else:
            session.rollback()
    except SQLAlchemyError:
        with suppress(SQLAlchemyError):
            session.invalidate()


def _rollback_quietly(session: Session) -> None:
    try:
        session.rollback()
    except SQLAlchemyError:
        with suppress(SQLAlchemyError):
            session.invalidate()
