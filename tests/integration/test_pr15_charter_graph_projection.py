import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from charteros.application.graph_projection import (
    PROJECTION_NAME,
    GraphProjectionConsistencyError,
    GraphProjectionGapError,
    consumer_name,
)
from charteros.application.outbox import OutboxDeliveryStatus, OutboxEnvelope
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphAggregateCursorRow,
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionCheckpointRow,
)
from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.shared.config import Settings

BASE = datetime(2001, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _document(
    *,
    event_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    payload: dict[str, object],
    recorded_at: datetime,
) -> str:
    timestamp = recorded_at.isoformat().replace("+00:00", "Z")
    value: dict[str, object] = {
        "event_id": str(event_id),
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "aggregate_version": aggregate_version,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": timestamp,
        "recorded_at": timestamp,
        "actor_id": None,
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _seed_event(
    factory: sessionmaker[Session],
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    payload: dict[str, object],
    ordinal: int,
) -> OutboxEnvelope:
    event_id = uuid4()
    recorded_at = BASE + timedelta(seconds=ordinal)
    canonical_json = _document(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload=payload,
        recorded_at=recorded_at,
    )
    with factory.begin() as session:
        session.add(
            OutboxEventRow(
                event_id=event_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                event_type=event_type,
                event_version=1,
                occurred_at=recorded_at,
                recorded_at=recorded_at,
                actor_id=None,
                correlation_id=None,
                causation_id=None,
                canonical_json=canonical_json,
                delivery_status=OutboxDeliveryStatus.PENDING.value,
                available_at=recorded_at,
                last_attempt_at=None,
                last_error=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                published_at=None,
                poisoned_at=None,
                publish_attempts=0,
                delivery_attempts=0,
            )
        )
    return OutboxEnvelope(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        event_version=1,
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=canonical_json,
    )


def _seed_nonprojected_event(factory: sessionmaker[Session], *, ordinal: int) -> UUID:
    envelope = _seed_event(
        factory,
        aggregate_type="pr15_test",
        aggregate_id=uuid4(),
        aggregate_version=1,
        event_type="PR15_TEST_EVENT",
        payload={},
        ordinal=ordinal,
    )
    return envelope.event_id


def _fake_envelope(
    *,
    event_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    canonical_json: str | None = None,
) -> OutboxEnvelope:
    recorded_at = BASE + timedelta(days=1)
    payload = {
        "operator_id": str(uuid4()),
        "registration": "SX-FAKE",
        "aircraft_type_id": str(uuid4()),
        "home_base_id": str(uuid4()),
        "status": "active",
    }
    document = canonical_json or _document(
        event_id=event_id,
        aggregate_type="aircraft",
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type="AIRCRAFT_REGISTERED",
        payload=payload,
        recorded_at=recorded_at,
    )
    return OutboxEnvelope(
        event_id=event_id,
        aggregate_type="aircraft",
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type="AIRCRAFT_REGISTERED",
        event_version=1,
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=document,
    )


@pytest.mark.integration
def test_event_to_node_and_edge_projection_with_restart_checkpoint() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 1501
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        operator_id = uuid4()
        airport_id = uuid4()
        aircraft_id = uuid4()
        operator = _seed_event(
            factory,
            aggregate_type="operator",
            aggregate_id=operator_id,
            aggregate_version=1,
            event_type="OPERATOR_REGISTERED",
            payload={
                "organization_id": str(uuid4()),
                "aoc_reference": "PR15-AOC",
                "operating_regions": ["EU"],
                "verification_status": "verified",
                "insurance_status": "valid",
                "commercial_status": "active",
            },
            ordinal=1501,
        )
        airport = _seed_event(
            factory,
            aggregate_type="airport",
            aggregate_id=airport_id,
            aggregate_version=1,
            event_type="AIRPORT_REGISTERED",
            payload={"icao": "LG15", "iata": None, "timezone": "Europe/Athens"},
            ordinal=1502,
        )
        aircraft = _seed_event(
            factory,
            aggregate_type="aircraft",
            aggregate_id=aircraft_id,
            aggregate_version=1,
            event_type="AIRCRAFT_REGISTERED",
            payload={
                "operator_id": str(operator_id),
                "registration": "SX-P15A",
                "aircraft_type_id": str(uuid4()),
                "home_base_id": str(airport_id),
                "status": "active",
            },
            ordinal=1503,
        )
        for item in (operator, airport, aircraft):
            assert store.consume_into_version(version, item, processed_at=item.recorded_at)

        restarted = SqlAlchemyGraphProjectionStore(factory)
        position = _seed_event(
            factory,
            aggregate_type="aircraft",
            aggregate_id=aircraft_id,
            aggregate_version=2,
            event_type="AIRCRAFT_POSITION_RECORDED",
            payload={
                "position_id": str(uuid4()),
                "airport_id": str(airport_id),
                "latitude": None,
                "longitude": None,
                "event_time": "2001-01-01T01:00:00Z",
                "knowledge_time": "2001-01-01T01:00:00Z",
                "source": "pr15-test",
                "provenance": {},
            },
            ordinal=1504,
        )
        assert restarted.consume_into_version(version, position, processed_at=position.recorded_at)

        with factory() as session:
            assert session.get(
                GraphNodeRow,
                (PROJECTION_NAME, version, "aircraft", aircraft_id),
            ) is not None
            edges = tuple(
                session.scalars(
                    select(GraphEdgeRow).where(
                        GraphEdgeRow.projection_name == PROJECTION_NAME,
                        GraphEdgeRow.projection_version == version,
                    )
                )
            )
            assert {edge.edge_type for edge in edges} >= {
                "OPERATES",
                "HOME_BASE",
                "HAS_POSITION",
                "AT_AIRPORT",
            }
            checkpoint = session.get(
                GraphProjectionCheckpointRow,
                (PROJECTION_NAME, version),
            )
            assert checkpoint is not None
            assert checkpoint.processed_event_count == 4
            cursor = session.get(
                GraphAggregateCursorRow,
                (PROJECTION_NAME, version, "aircraft", aircraft_id),
            )
            assert cursor is not None
            assert cursor.last_aggregate_version == 2
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_duplicate_and_concurrent_duplicate_delivery_are_idempotent() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 1502
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        event = _seed_event(
            factory,
            aggregate_type="airport",
            aggregate_id=uuid4(),
            aggregate_version=1,
            event_type="AIRPORT_REGISTERED",
            payload={"icao": "LG16", "iata": None, "timezone": "Europe/Athens"},
            ordinal=1510,
        )
        barrier = Barrier(2)

        def consume() -> bool:
            barrier.wait()
            return SqlAlchemyGraphProjectionStore(factory).consume_into_version(
                version,
                event,
                processed_at=event.recorded_at,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(consume)
            second = executor.submit(consume)
            assert sorted((first.result(), second.result())) == [False, True]

        assert not store.consume_into_version(version, event, processed_at=event.recorded_at)
        with factory() as session:
            receipt_total = session.scalar(
                select(func.count())
                .select_from(OutboxConsumerReceiptRow)
                .where(OutboxConsumerReceiptRow.consumer_name == consumer_name(version))
            )
            checkpoint = session.get(
                GraphProjectionCheckpointRow,
                (PROJECTION_NAME, version),
            )
            assert receipt_total == 1
            assert checkpoint is not None
            assert checkpoint.processed_event_count == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_projection_version_isolation() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    first_version = 1503
    second_version = 1504
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(first_version, now=BASE)
        store.prepare_version(second_version, now=BASE)
        event = _seed_event(
            factory,
            aggregate_type="airport",
            aggregate_id=uuid4(),
            aggregate_version=1,
            event_type="AIRPORT_REGISTERED",
            payload={"icao": "LG17", "iata": None, "timezone": "Europe/Athens"},
            ordinal=1520,
        )
        assert store.consume_into_version(first_version, event, processed_at=event.recorded_at)
        assert store.consume_into_version(second_version, event, processed_at=event.recorded_at)

        with factory() as session:
            for projection_version in (first_version, second_version):
                assert session.get(
                    GraphNodeRow,
                    (PROJECTION_NAME, projection_version, "airport", event.aggregate_id),
                ) is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_gap_detection_and_projection_failure_roll_back_receipt_and_checkpoint() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    version = 1505
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        store.prepare_version(version, now=BASE)
        backing_event_id = _seed_nonprojected_event(factory, ordinal=1530)
        aggregate_id = uuid4()
        gap = _fake_envelope(
            event_id=backing_event_id,
            aggregate_id=aggregate_id,
            aggregate_version=2,
        )
        with pytest.raises(GraphProjectionGapError, match="expected version 1, got 2"):
            store.consume_into_version(version, gap, processed_at=gap.recorded_at)

        malformed_event_id = _seed_nonprojected_event(factory, ordinal=1531)
        malformed = _fake_envelope(
            event_id=malformed_event_id,
            aggregate_id=uuid4(),
            aggregate_version=1,
            canonical_json="{}",
        )
        with pytest.raises(GraphProjectionConsistencyError):
            store.consume_into_version(version, malformed, processed_at=malformed.recorded_at)

        with factory() as session:
            checkpoint = session.get(
                GraphProjectionCheckpointRow,
                (PROJECTION_NAME, version),
            )
            assert checkpoint is not None
            assert checkpoint.processed_event_count == 0
            receipts = session.scalar(
                select(func.count())
                .select_from(OutboxConsumerReceiptRow)
                .where(OutboxConsumerReceiptRow.consumer_name == consumer_name(version))
            )
            assert receipts == 0
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.regression
def test_full_rebuild_is_deterministic_and_verification_detects_corruption() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    first_version = 1506
    second_version = 1507
    try:
        store = SqlAlchemyGraphProjectionStore(factory)
        first = store.rebuild(first_version, now=BASE + timedelta(days=2))
        second = store.rebuild(second_version, now=BASE + timedelta(days=2))
        assert first.ok, first.issues
        assert second.ok, second.issues
        assert first.reference_digest == first.persisted_digest
        assert second.reference_digest == second.persisted_digest
        assert first.persisted_digest == second.persisted_digest
        assert first.event_count == second.event_count

        with factory.begin() as session:
            session.add(
                GraphEdgeRow(
                    projection_name=PROJECTION_NAME,
                    projection_version=second_version,
                    edge_type="CORRUPT_REFERENCE",
                    source_type="missing_source",
                    source_id=uuid4(),
                    target_type="missing_target",
                    target_id=uuid4(),
                    attributes={},
                    source_aggregate_type="pr15_corruption_test",
                    source_aggregate_id=uuid4(),
                    source_aggregate_version=1,
                    last_event_id=uuid4(),
                    updated_at=BASE + timedelta(days=2),
                )
            )

        corrupted = store.verify(second_version)
        assert not corrupted.ok
        assert any("invalid edge references" in issue for issue in corrupted.issues)
        assert any(
            "deterministic reference rebuild digest mismatch" in issue
            for issue in corrupted.issues
        )
    finally:
        engine.dispose()
