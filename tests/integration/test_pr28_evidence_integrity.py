from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from charteros.infrastructure.db.evidence_integrity import (
    create_evidence_checkpoint,
    verify_evidence_integrity,
)
from charteros.shared.config import Settings


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _engine() -> Engine:
    return create_engine(_settings().database_url)


@contextmanager
def _owner_trigger_disabled(
    connection: Connection,
    *,
    table: str,
    trigger: str,
) -> Iterator[None]:
    connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    try:
        yield
    finally:
        connection.execute(text(f"ALTER TABLE {table} ENABLE ALWAYS TRIGGER {trigger}"))


def _insert_fx_rate(
    connection: Connection,
    *,
    source: str,
    fx_timestamp: datetime,
    revision: int = 1,
    supersedes: UUID | None = None,
    rate: str = "0.8421",
) -> UUID:
    rate_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO fx_rates (
                id, version, source_currency, target_currency, rate_text,
                source_minor_exponent, target_minor_exponent, fx_source,
                fx_source_version, fx_timestamp, recorded_at, revision_number,
                supersedes_rate_id
            ) VALUES (
                :id, 1, 'USD', 'EUR', :rate,
                2, 2, :source, :source_version, :fx_timestamp, :recorded_at,
                :revision, :supersedes
            )
            """
        ),
        {
            "id": rate_id,
            "rate": rate,
            "source": source,
            "source_version": f"{source}-v{revision}",
            "fx_timestamp": fx_timestamp,
            "recorded_at": fx_timestamp + timedelta(seconds=revision),
            "revision": revision,
            "supersedes": supersedes,
        },
    )
    return rate_id


def _insert_outbox_event(connection: Connection) -> UUID:
    event_id = uuid4()
    aggregate_id = uuid4()
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO outbox_events (
                event_id, aggregate_type, aggregate_id, aggregate_version,
                event_type, event_version, occurred_at, recorded_at,
                actor_id, correlation_id, causation_id, canonical_json,
                published_at, publish_attempts, delivery_status, available_at,
                last_attempt_at, last_error, lease_owner, lease_token,
                lease_expires_at, poisoned_at, delivery_attempts
            ) VALUES (
                :event_id, 'pr28_test', :aggregate_id, 1,
                'PR28_TEST_EVENT', 1, :now, :now,
                NULL, NULL, NULL, '{"event_type":"PR28_TEST_EVENT"}',
                NULL, 0, 'pending', :now,
                NULL, NULL, NULL, NULL, NULL, NULL, 0
            )
            """
        ),
        {"event_id": event_id, "aggregate_id": aggregate_id, "now": now},
    )
    return event_id


def _stream_for_source(connection: Connection, *, table: str, source_id: UUID) -> str:
    return str(
        connection.execute(
            text(
                """
                SELECT stream_key
                FROM evidence_integrity_entries
                WHERE source_table = :table
                  AND source_key ->> 'id' = :source_id
                """
            ),
            {"table": table, "source_id": str(source_id)},
        ).scalar_one()
    )


@pytest.mark.integration
def test_pr28_plain_sql_update_delete_and_outbox_envelope_guard() -> None:
    engine = _engine()
    try:
        source = f"pr28-guard-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=1)
        with engine.begin() as connection:
            rate_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
            )
            event_id = _insert_outbox_event(connection)

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("UPDATE fx_rates SET rate_text = '9.99' WHERE id = :id"),
                {"id": rate_id},
            )

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("DELETE FROM fx_rates WHERE id = :id"),
                {"id": rate_id},
            )

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE outbox_events SET last_error = 'retryable' WHERE event_id = :id"),
                {"id": event_id},
            )

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE outbox_events "
                    'SET canonical_json = \'{"event_type":"TAMPERED"}\' '
                    "WHERE event_id = :id"
                ),
                {"id": event_id},
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_append_only_fx_correction_builds_valid_lineage() -> None:
    engine = _engine()
    try:
        source = f"pr28-correction-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=2)
        with engine.begin() as connection:
            first_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
                revision=1,
                rate="0.8421",
            )
            second_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
                revision=2,
                supersedes=first_id,
                rate="0.8500",
            )
            stream_key = _stream_for_source(
                connection,
                table="fx_rates",
                source_id=first_id,
            )
            entries = (
                connection.execute(
                    text(
                        """
                        SELECT sequence, previous_digest, digest, source_key
                        FROM evidence_integrity_entries
                        WHERE stream_key = :stream_key
                        ORDER BY sequence
                        """
                    ),
                    {"stream_key": stream_key},
                )
                .mappings()
                .all()
            )

        assert len(entries) == 2
        assert [int(item["sequence"]) for item in entries] == [1, 2]
        assert entries[0]["previous_digest"] is None
        assert entries[1]["previous_digest"] == entries[0]["digest"]
        assert entries[1]["source_key"]["id"] == str(second_id)

        with Session(engine) as session:
            assert verify_evidence_integrity(session, stream_key=stream_key) == ()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_privileged_historical_source_tamper_is_detected_and_restorable() -> None:
    engine = _engine()
    try:
        source = f"pr28-source-tamper-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=3)
        with engine.begin() as connection:
            rate_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
            )
            stream_key = _stream_for_source(
                connection,
                table="fx_rates",
                source_id=rate_id,
            )
            with _owner_trigger_disabled(
                connection,
                table="fx_rates",
                trigger="trg_ei_fx_rate_guard",
            ):
                connection.execute(
                    text("UPDATE fx_rates SET rate_text = '0.9999' WHERE id = :id"),
                    {"id": rate_id},
                )

        try:
            with Session(engine) as session:
                violations = verify_evidence_integrity(session, stream_key=stream_key)
            assert any(item.violation == "source_payload_mismatch" for item in violations)
        finally:
            with (
                engine.begin() as connection,
                _owner_trigger_disabled(
                    connection,
                    table="fx_rates",
                    trigger="trg_ei_fx_rate_guard",
                ),
            ):
                connection.execute(
                    text("UPDATE fx_rates SET rate_text = '0.8421' WHERE id = :id"),
                    {"id": rate_id},
                )

        with Session(engine) as session:
            assert verify_evidence_integrity(session, stream_key=stream_key) == ()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_tampered_digest_is_detected() -> None:
    engine = _engine()
    try:
        source = f"pr28-digest-tamper-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=4)
        with engine.begin() as connection:
            rate_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
            )
            stream_key = _stream_for_source(
                connection,
                table="fx_rates",
                source_id=rate_id,
            )
            original_digest = str(
                connection.execute(
                    text(
                        """
                        SELECT digest
                        FROM evidence_integrity_entries
                        WHERE stream_key = :stream_key AND sequence = 1
                        """
                    ),
                    {"stream_key": stream_key},
                ).scalar_one()
            )
            with _owner_trigger_disabled(
                connection,
                table="evidence_integrity_entries",
                trigger="trg_ei_entries_append_only",
            ):
                connection.execute(
                    text(
                        """
                        UPDATE evidence_integrity_entries
                        SET digest = :tampered
                        WHERE stream_key = :stream_key AND sequence = 1
                        """
                    ),
                    {"stream_key": stream_key, "tampered": "0" * 64},
                )

        try:
            with Session(engine) as session:
                violations = verify_evidence_integrity(session, stream_key=stream_key)
            assert any(item.violation == "digest_mismatch" for item in violations)
        finally:
            with (
                engine.begin() as connection,
                _owner_trigger_disabled(
                    connection,
                    table="evidence_integrity_entries",
                    trigger="trg_ei_entries_append_only",
                ),
            ):
                connection.execute(
                    text(
                        """
                        UPDATE evidence_integrity_entries
                        SET digest = :digest
                        WHERE stream_key = :stream_key AND sequence = 1
                        """
                    ),
                    {"stream_key": stream_key, "digest": original_digest},
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_broken_previous_digest_lineage_is_detected() -> None:
    engine = _engine()
    try:
        source = f"pr28-lineage-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=5)
        with engine.begin() as connection:
            first_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
                revision=1,
            )
            _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
                revision=2,
                supersedes=first_id,
                rate="0.8430",
            )
            stream_key = _stream_for_source(
                connection,
                table="fx_rates",
                source_id=first_id,
            )
            original_previous = str(
                connection.execute(
                    text(
                        """
                        SELECT previous_digest
                        FROM evidence_integrity_entries
                        WHERE stream_key = :stream_key AND sequence = 2
                        """
                    ),
                    {"stream_key": stream_key},
                ).scalar_one()
            )
            with _owner_trigger_disabled(
                connection,
                table="evidence_integrity_entries",
                trigger="trg_ei_entries_append_only",
            ):
                connection.execute(
                    text(
                        """
                        UPDATE evidence_integrity_entries
                        SET previous_digest = :tampered
                        WHERE stream_key = :stream_key AND sequence = 2
                        """
                    ),
                    {"stream_key": stream_key, "tampered": "f" * 64},
                )

        try:
            with Session(engine) as session:
                violations = verify_evidence_integrity(session, stream_key=stream_key)
            assert any(item.violation == "previous_digest_mismatch" for item in violations)
        finally:
            with (
                engine.begin() as connection,
                _owner_trigger_disabled(
                    connection,
                    table="evidence_integrity_entries",
                    trigger="trg_ei_entries_append_only",
                ),
            ):
                connection.execute(
                    text(
                        """
                        UPDATE evidence_integrity_entries
                        SET previous_digest = :previous
                        WHERE stream_key = :stream_key AND sequence = 2
                        """
                    ),
                    {"stream_key": stream_key, "previous": original_previous},
                )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr28_concurrent_same_stream_appends_are_serialized_without_global_chain() -> None:
    engine = _engine()
    subject_id = uuid4()
    barrier = Barrier(2)

    def append(ordinal: int) -> None:
        snapshot_id = uuid4()
        source_aggregate_id = uuid4()
        decided_at = datetime.now(UTC) + timedelta(microseconds=ordinal)
        barrier.wait()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO decision_evidence_snapshots (
                        id, decision_type, subject_type, subject_id,
                        source_aggregate_type, source_aggregate_id,
                        schema_version, decided_at, known_as_of, actor_id,
                        correlation_id, policy_versions, canonical_json,
                        integrity_digest
                    ) VALUES (
                        :id, 'pr28_concurrent', 'mission', :subject_id,
                        'pr28_source', :source_aggregate_id,
                        'pr28.v1', :decided_at, NULL, NULL,
                        NULL, '{}'::json, :canonical_json, :integrity_digest
                    )
                    """
                ),
                {
                    "id": snapshot_id,
                    "subject_id": subject_id,
                    "source_aggregate_id": source_aggregate_id,
                    "decided_at": decided_at,
                    "canonical_json": f'{{"ordinal":{ordinal}}}',
                    "integrity_digest": f"{ordinal}" * 64,
                },
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(append, ordinal) for ordinal in (1, 2)]
            for future in futures:
                future.result()

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT stream_key, sequence
                    FROM evidence_integrity_entries
                    WHERE source_table = 'decision_evidence_snapshots'
                      AND canonical_payload ->> 'subject_id' = :subject_id
                    ORDER BY sequence
                    """
                ),
                {"subject_id": str(subject_id)},
            ).all()
        assert len(rows) == 2
        assert [int(row.sequence) for row in rows] == [1, 2]
        stream_key = str(rows[0].stream_key)

        with Session(engine) as session:
            assert verify_evidence_integrity(session, stream_key=stream_key) == ()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_verification_and_checkpoint_rebuild_are_deterministic() -> None:
    engine = _engine()
    try:
        source = f"pr28-checkpoint-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=6)
        with engine.begin() as connection:
            first_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
            )
            _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
                revision=2,
                supersedes=first_id,
                rate="0.8450",
            )
            stream_key = _stream_for_source(
                connection,
                table="fx_rates",
                source_id=first_id,
            )

        with Session(engine) as session, session.begin():
            first = create_evidence_checkpoint(session, stream_key=stream_key)
            second = create_evidence_checkpoint(session, stream_key=stream_key)
            assert first.id == second.id
            assert first.root_digest == second.root_digest

        with Session(engine) as session:
            run_one = verify_evidence_integrity(session, stream_key=stream_key)
            run_two = verify_evidence_integrity(session, stream_key=stream_key)
        assert run_one == run_two == ()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr28_runtime_role_has_restricted_evidence_privileges() -> None:
    engine = _engine()
    role = f"charteros_runtime_pr28_{uuid4().hex[:10]}"
    try:
        source = f"pr28-role-{uuid4().hex}"
        observed_at = datetime.now(UTC) - timedelta(minutes=7)
        with engine.begin() as connection:
            rate_id = _insert_fx_rate(
                connection,
                source=source,
                fx_timestamp=observed_at,
            )
            event_id = _insert_outbox_event(connection)
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER'))
            connection.execute(
                text("SELECT charteros_apply_runtime_evidence_privileges(CAST(:role AS name))"),
                {"role": role},
            )

            assert not bool(
                connection.exec_driver_sql(
                    f"SELECT has_table_privilege('{role}', 'fx_rates', 'UPDATE')"
                ).scalar_one()
            )
            assert bool(
                connection.exec_driver_sql(
                    f"SELECT has_table_privilege('{role}', 'outbox_events', 'UPDATE')"
                ).scalar_one()
            )
            assert not bool(
                connection.exec_driver_sql(
                    f"SELECT has_table_privilege('{role}', 'evidence_integrity_entries', 'INSERT')"
                ).scalar_one()
            )

        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert (
                int(
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM evidence_integrity_entries"
                    ).scalar_one()
                )
                >= 2
            )
            connection.execute(
                text("UPDATE outbox_events SET last_error = 'role-smoke' WHERE event_id = :id"),
                {"id": event_id},
            )
            assert (
                int(
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM charteros_verify_evidence_integrity(NULL)"
                    ).scalar_one()
                )
                == 0
            )

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            connection.execute(
                text("UPDATE fx_rates SET rate_text = '1.2345' WHERE id = :id"),
                {"id": rate_id},
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        engine.dispose()
