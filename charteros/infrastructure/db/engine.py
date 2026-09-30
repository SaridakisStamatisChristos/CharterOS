from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from charteros.shared.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Build the canonical PostgreSQL engine without opening a connection eagerly."""
    connect_args: dict[str, Any] = {
        "connect_timeout": settings.database_connect_timeout_seconds,
    }
    if settings.database_runtime_role is not None:
        connect_args["options"] = f"-c role={settings.database_runtime_role}"
    return create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
        hide_parameters=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
