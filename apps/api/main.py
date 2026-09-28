from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from charteros import __version__
from charteros.shared.config import Settings, get_settings
from charteros.shared.logging import configure_logging, get_logger
from charteros.shared.middleware import CorrelationIdMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    logger = get_logger(__name__)
    logger.info(
        "application_started",
        extra={"event": "application_started", "environment": settings.environment},
    )
    yield
    logger.info("application_stopped", extra={"event": "application_stopped"})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)

    app = FastAPI(
        title="CharterOS API",
        version=__version__,
        lifespan=_lifespan,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": resolved_settings.service_name,
            "version": __version__,
            "environment": resolved_settings.environment,
        }

    return app


app = create_app()
