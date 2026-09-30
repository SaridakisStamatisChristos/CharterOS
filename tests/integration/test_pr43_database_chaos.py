from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import IdempotencyRecordRow
from charteros.infrastructure.db.failures import DatabaseFailureKind, DatabaseTransactionError
from charteros.infrastructure.db.transactions import run_transaction
from charteros.shared.config import Settings


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


@pytest.mark.integration
def test_pr43_backend_termination_retries_once_at_outer_transaction_boundary() -> None:
    settings = _settings()
    engine = build_engine(settings)
    admin_engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            attempts = 0

            def action() -> int:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(backend_pid, int)
                    with admin_engine.begin() as admin:
                        terminated = admin.scalar(
                            text("SELECT pg_terminate_backend(:backend_pid)"),
                            {"backend_pid": backend_pid},
                        )
                        assert terminated is True
                    # The next statement uses the killed connection and must fail before commit.
                    session.execute(text("SELECT 1")).scalar_one()
                return int(session.execute(text("SELECT 1")).scalar_one())

            assert run_transaction(session, action) == 1
            assert attempts == 2
    finally:
        admin_engine.dispose()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr43_real_deadlock_retries_exactly_one_victim_once() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    barrier = Barrier(2)

    def contend(first_lock: int, second_lock: int) -> int:
        with factory() as session:
            attempts = 0

            def action() -> int:
                nonlocal attempts
                attempts += 1
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": first_lock},
                )
                if attempts == 1:
                    barrier.wait(timeout=10)
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": second_lock},
                )
                return attempts

            run_transaction(session, action)
            return attempts

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(contend, 9_430_001, 9_430_002)
            second = executor.submit(contend, 9_430_002, 9_430_001)
            attempt_counts = sorted((first.result(), second.result()))

        assert attempt_counts == [1, 2]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_lock_timeout_retries_are_bounded() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    lock_key = 9_430_003
    locker = engine.connect()
    locker_transaction = locker.begin()
    try:
        locker.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

        with factory() as session:
            attempts = 0

            def action() -> None:
                nonlocal attempts
                attempts += 1
                session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )

            started = monotonic()
            with pytest.raises(DatabaseTransactionError) as captured:
                run_transaction(session, action)
            elapsed = monotonic() - started

        assert attempts == 2
        assert captured.value.failure.kind is DatabaseFailureKind.SAFE_TRANSIENT
        assert captured.value.failure.sqlstate == "55P03"
        assert elapsed < 2.0
    finally:
        locker_transaction.rollback()
        locker.close()
        engine.dispose()


@pytest.mark.integration
def test_pr43_pool_exhaustion_fails_within_configured_checkout_timeout() -> None:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    settings = Settings(
        environment="test",
        database_url=database_url,
        database_pool_size=1,
        database_max_overflow=0,
        database_pool_timeout_seconds=0.1,
        _env_file=None,
    )
    engine = build_engine(settings)
    held = engine.connect()
    try:
        started = monotonic()
        with pytest.raises(SQLAlchemyTimeoutError):
            engine.connect()
        elapsed = monotonic() - started
        assert elapsed < 1.0
    finally:
        held.close()
        engine.dispose()


@pytest.mark.integration
def test_pr43_commit_success_with_lost_commit_response_reconciles_canonical_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    scope = "pr43:ambiguous-commit"
    key = str(uuid4())
    try:
        with factory() as session:
            original_commit = session.commit
            commit_calls = 0

            def commit_then_lose_response() -> None:
                nonlocal commit_calls
                commit_calls += 1
                original_commit()
                if commit_calls == 1:
                    raise OperationalError(
                        "COMMIT",
                        {},
                        _SqlStateError("08006"),
                        connection_invalidated=True,
                    )

            monkeypatch.setattr(session, "commit", commit_then_lose_response)

            def action() -> str:
                session.add(
                    IdempotencyRecordRow(
                        scope=scope,
                        key=key,
                        request_hash="a" * 64,
                        status_code=201,
                        response_body={"result": "canonical"},
                    )
                )
                return "unobserved-original-response"

            def reconcile() -> str | None:
                row = session.get(IdempotencyRecordRow, (scope, key))
                if row is None:
                    return None
                return str(row.response_body["result"])

            result = run_transaction(
                session,
                action,
                reconcile_ambiguous=reconcile,
            )

        assert result == "canonical"
        assert commit_calls == 2
        with factory() as verification:
            row = verification.get(IdempotencyRecordRow, (scope, key))
            assert row is not None
            assert row.response_body == {"result": "canonical"}
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_statement_timeout_retries_are_bounded() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            attempts = 0

            def action() -> None:
                nonlocal attempts
                attempts += 1
                session.execute(text("SET LOCAL statement_timeout = '100ms'"))
                session.execute(text("SELECT pg_sleep(1)"))

            started = monotonic()
            with pytest.raises(DatabaseTransactionError) as captured:
                run_transaction(session, action)
            elapsed = monotonic() - started

        assert attempts == 2
        assert captured.value.failure.kind is DatabaseFailureKind.SAFE_TRANSIENT
        assert captured.value.failure.sqlstate == "57014"
        assert elapsed < 2.0
    finally:
        engine.dispose()
