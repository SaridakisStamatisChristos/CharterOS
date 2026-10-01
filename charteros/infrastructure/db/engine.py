from time import perf_counter
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import Session, sessionmaker

from charteros.observability import OperationalMetrics, get_operational_metrics
from charteros.shared.config import Settings


def build_engine(
    settings: Settings,
    *,
    metrics: OperationalMetrics | None = None,
) -> Engine:
    """Build the canonical PostgreSQL engine without opening a connection eagerly."""
    resolved_metrics = metrics or get_operational_metrics()

    class ObservedQueuePool(QueuePool):
        def _do_get(self) -> Any:
            started = perf_counter()
            try:
                connection_record = super()._do_get()
            except Exception:
                resolved_metrics.db_pool_checkout(
                    duration_seconds=perf_counter() - started,
                    outcome="failure",
                )
                resolved_metrics.db_connectivity_failure()
                raise
            resolved_metrics.db_pool_checkout(
                duration_seconds=perf_counter() - started,
                outcome="success",
            )
            return connection_record

    connect_args: dict[str, Any] = {
        "connect_timeout": settings.database_connect_timeout_seconds,
    }
    if settings.database_runtime_role is not None:
        connect_args["options"] = f"-c role={settings.database_runtime_role}"
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        poolclass=ObservedQueuePool,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
        hide_parameters=True,
    )

    def record_pool_state() -> None:
        pool = engine.pool
        if isinstance(pool, QueuePool):
            resolved_metrics.db_pool_state(
                checked_out=pool.checkedout(),
                pool_size=pool.size(),
                overflow=max(pool.overflow(), 0),
            )

    @event.listens_for(engine.pool, "checkout")
    def on_checkout(*_args: object) -> None:
        record_pool_state()

    @event.listens_for(engine.pool, "checkin")
    def on_checkin(*_args: object) -> None:
        record_pool_state()

    record_pool_state()
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
