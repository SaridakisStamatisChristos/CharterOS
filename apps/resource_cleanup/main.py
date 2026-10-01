from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.repositories.abuse import SqlAlchemyRateBudgetRepository
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository
from charteros.shared.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class CleanupResult:
    idempotency_deleted: int
    rate_windows_deleted: int
    idempotency_cutoff: datetime


def cleanup_resource_state(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> CleanupResult:
    operation_time = now or datetime.now(UTC)
    if operation_time.tzinfo is None or operation_time.utcoffset() is None:
        raise ValueError("cleanup time must be timezone-aware")
    cutoff = operation_time - timedelta(days=settings.idempotency_retention_days)

    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory.begin() as session:
            idempotency_deleted = SqlAlchemyIdempotencyRepository(session).delete_created_before(
                cutoff=cutoff,
                limit=settings.idempotency_cleanup_batch_size,
            )
            rate_windows_deleted = SqlAlchemyRateBudgetRepository(
                session
            ).delete_windows_older_than(
                retention_seconds=settings.api_rate_limit_window_retention_seconds,
                limit=settings.idempotency_cleanup_batch_size,
            )
        return CleanupResult(
            idempotency_deleted=idempotency_deleted,
            rate_windows_deleted=rate_windows_deleted,
            idempotency_cutoff=cutoff,
        )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete expired idempotency and transient API rate-window state."
    )
    parser.parse_args()
    result = cleanup_resource_state(get_settings())
    print(
        json.dumps(
            {
                "idempotency_deleted": result.idempotency_deleted,
                "rate_windows_deleted": result.rate_windows_deleted,
                "idempotency_cutoff": result.idempotency_cutoff.isoformat(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
