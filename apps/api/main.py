from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from apps.api.routes import (
    catalog_router,
    fleet_router,
    matching_router,
    mission_router,
    rfq_router,
)
from charteros import __version__
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.infrastructure.db.engine import build_engine, build_session_factory
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
    app.state.engine.dispose()
    logger.info("application_stopped", extra={"event": "application_stopped"})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    engine = build_engine(resolved_settings)

    app = FastAPI(
        title="CharterOS API",
        version=__version__,
        lifespan=_lifespan,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(catalog_router)
    app.include_router(fleet_router)
    app.include_router(mission_router)
    app.include_router(matching_router)
    app.include_router(rfq_router)

    @app.exception_handler(DomainValidationError)
    async def domain_validation_handler(
        _request: Request, exc: DomainValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(EntityNotFoundError)
    async def not_found_handler(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(EntityConflictError)
    async def conflict_handler(_request: Request, exc: EntityConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

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
