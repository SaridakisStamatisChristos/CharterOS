from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from charteros.infrastructure.db.failures import (
    DatabaseFailureKind,
    DatabaseFailurePhase,
    classify_database_failure,
)


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "55P03", "57014"])
def test_known_rollback_safe_sqlstates_are_retryable(sqlstate: str) -> None:
    exc = OperationalError("SELECT 1", {}, _SqlStateError(sqlstate))

    failure = classify_database_failure(exc, phase=DatabaseFailurePhase.EXECUTION)

    assert failure.kind is DatabaseFailureKind.SAFE_TRANSIENT
    assert failure.sqlstate == sqlstate
    assert failure.retryable is True


def test_connection_loss_before_commit_is_retryable() -> None:
    exc = OperationalError(
        "SELECT 1",
        {},
        _SqlStateError("08006"),
        connection_invalidated=True,
    )

    failure = classify_database_failure(exc, phase=DatabaseFailurePhase.EXECUTION)

    assert failure.kind is DatabaseFailureKind.SAFE_TRANSIENT
    assert failure.retryable is True


def test_connection_loss_during_commit_is_ambiguous_and_not_retried() -> None:
    exc = OperationalError(
        "COMMIT",
        {},
        _SqlStateError("08006"),
        connection_invalidated=True,
    )

    failure = classify_database_failure(exc, phase=DatabaseFailurePhase.COMMIT)

    assert failure.kind is DatabaseFailureKind.AMBIGUOUS_COMMIT
    assert failure.retryable is False


def test_integrity_violation_fails_closed() -> None:
    exc = IntegrityError("INSERT", {}, _SqlStateError("23505"))

    failure = classify_database_failure(exc, phase=DatabaseFailurePhase.EXECUTION)

    assert failure.kind is DatabaseFailureKind.INTEGRITY_VIOLATION
    assert failure.retryable is False


def test_pool_checkout_timeout_is_not_retried_in_process() -> None:
    failure = classify_database_failure(
        SQLAlchemyTimeoutError("pool exhausted"),
        phase=DatabaseFailurePhase.EXECUTION,
    )

    assert failure.kind is DatabaseFailureKind.PERMANENT_INFRASTRUCTURE
    assert failure.retryable is False
