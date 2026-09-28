from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from charteros.shared.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Build the canonical PostgreSQL engine without opening a connection eagerly."""
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
