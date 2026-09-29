from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from charteros.application.graph_projection import is_projected_aggregate
from charteros.application.outbox import OutboxEnvelope
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore


class GraphProjectionPublisher:
    """PR14 outbox publisher that applies the active Charter Graph projection atomically."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._store = SqlAlchemyGraphProjectionStore(session_factory)

    def publish(self, envelope: OutboxEnvelope) -> None:
        if not is_projected_aggregate(envelope.aggregate_type):
            return
        self._store.consume_active(envelope, processed_at=datetime.now(UTC))
