from sqlalchemy import Engine, create_engine

from charteros.shared.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Build the canonical PostgreSQL engine without opening a connection eagerly."""
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )
