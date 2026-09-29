from __future__ import annotations

from charteros.application.outbox import OutboxEnvelope
from charteros.shared.logging import get_logger


class JsonLogOutboxPublisher:
    """Structured-log transport used by the reference worker composition.

    Delivery semantics are defined by the worker and durable PostgreSQL metadata. This adapter can
    be replaced by a broker or in-process projection dispatcher without changing the outbox engine.
    """

    def __init__(self) -> None:
        self._logger = get_logger(__name__)

    def publish(self, envelope: OutboxEnvelope) -> None:
        self._logger.info(
            "outbox_event_published",
            extra={
                "event": "outbox_event_published",
                "event_id": str(envelope.event_id),
                "aggregate_type": envelope.aggregate_type,
                "aggregate_id": str(envelope.aggregate_id),
                "aggregate_version": envelope.aggregate_version,
                "event_type": envelope.event_type,
                "event_version": envelope.event_version,
                "canonical_event": envelope.canonical_json,
            },
        )
