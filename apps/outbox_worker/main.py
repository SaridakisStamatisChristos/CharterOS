from __future__ import annotations

import argparse
import os
import signal
import socket
from datetime import UTC, datetime
from threading import Event
from uuid import UUID, uuid4

from sqlalchemy import Engine

from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.repositories.outbox import SqlAlchemyOutboxDeliveryRepository
from charteros.infrastructure.outbox import JsonLogOutboxPublisher
from charteros.outbox import OutboxWorker, OutboxWorkerConfig
from charteros.shared.config import Settings, get_settings
from charteros.shared.logging import configure_logging, get_logger


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def build_worker(
    settings: Settings,
    *,
    worker_id: str | None = None,
) -> tuple[Engine, OutboxWorker]:
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    worker = OutboxWorker(
        repository=SqlAlchemyOutboxDeliveryRepository(session_factory),
        publisher=JsonLogOutboxPublisher(),
        worker_id=worker_id or _worker_id(),
        config=OutboxWorkerConfig(
            batch_size=settings.outbox_batch_size,
            lease_seconds=settings.outbox_lease_seconds,
            max_attempts=settings.outbox_max_attempts,
            backoff_base_seconds=settings.outbox_backoff_base_seconds,
            backoff_max_seconds=settings.outbox_backoff_max_seconds,
            poll_interval_seconds=settings.outbox_poll_interval_seconds,
        ),
    )
    return engine, worker


def main() -> None:
    parser = argparse.ArgumentParser(description="CharterOS transactional outbox worker")
    parser.add_argument("--once", action="store_true", help="Process one due batch and exit")
    parser.add_argument(
        "--requeue-poison",
        type=UUID,
        help="Explicitly requeue one poisoned event before normal worker execution",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    logger = get_logger(__name__)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    repository = SqlAlchemyOutboxDeliveryRepository(session_factory)
    worker = OutboxWorker(
        repository=repository,
        publisher=JsonLogOutboxPublisher(),
        worker_id=_worker_id(),
        config=OutboxWorkerConfig(
            batch_size=settings.outbox_batch_size,
            lease_seconds=settings.outbox_lease_seconds,
            max_attempts=settings.outbox_max_attempts,
            backoff_base_seconds=settings.outbox_backoff_base_seconds,
            backoff_max_seconds=settings.outbox_backoff_max_seconds,
            poll_interval_seconds=settings.outbox_poll_interval_seconds,
        ),
    )

    try:
        if args.requeue_poison is not None:
            requeued = repository.requeue_poison(
                event_id=args.requeue_poison,
                available_at=_now(),
            )
            logger.info(
                "outbox_poison_requeue",
                extra={
                    "event": "outbox_poison_requeue",
                    "event_id": str(args.requeue_poison),
                    "requeued": requeued,
                },
            )

        if args.once:
            result = worker.run_once()
            logger.info(
                "outbox_worker_once_complete",
                extra={
                    "event": "outbox_worker_once_complete",
                    "claimed": result.claimed,
                    "delivered": result.delivered,
                    "retried": result.retried,
                    "poisoned": result.poisoned,
                    "lease_lost": result.lease_lost,
                },
            )
            return

        stop_event = Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        logger.info("outbox_worker_started", extra={"event": "outbox_worker_started"})
        worker.run_forever(stop_event)
        logger.info("outbox_worker_stopped", extra={"event": "outbox_worker_stopped"})
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":
    main()
