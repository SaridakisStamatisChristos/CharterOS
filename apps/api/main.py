from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from apps.api.routes import (
    booking_router,
    buyer_portal_router,
    catalog_router,
    contract_router,
    disruption_router,
    evidence_router,
    fleet_router,
    fx_router,
    graph_query_router,
    matching_router,
    mission_router,
    operator_portal_router,
    quote_router,
    reconciliation_router,
    repositioning_router,
    rfq_router,
    tender_router,
)
from apps.api.security import authorize_request, build_auth_backend
from charteros import __version__
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.security.auth import AuthenticationBackend
from charteros.shared.clock import Clock, SystemClock
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


def create_app(
    settings: Settings | None = None,
    *,
    auth_backend: AuthenticationBackend | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    engine = build_engine(resolved_settings)

    app = FastAPI(
        title="CharterOS API",
        version=__version__,
        lifespan=_lifespan,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        openapi_url=("/openapi.json" if resolved_settings.environment != "production" else None),
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.settings = resolved_settings
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.auth_backend = (
        auth_backend if auth_backend is not None else build_auth_backend(resolved_settings)
    )
    app.add_middleware(CorrelationIdMiddleware)

    secured_routers = (
        catalog_router,
        buyer_portal_router,
        fleet_router,
        fx_router,
        graph_query_router,
        mission_router,
        matching_router,
        operator_portal_router,
        rfq_router,
        tender_router,
        quote_router,
        reconciliation_router,
        repositioning_router,
        booking_router,
        contract_router,
        disruption_router,
        evidence_router,
    )
    for router in secured_routers:
        app.include_router(router, dependencies=[Depends(authorize_request)])

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

    @app.get("/ready", tags=["system"])
    def readiness(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
        except SQLAlchemyError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable"},
            )
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})

    return app


app = create_app()
