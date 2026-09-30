from __future__ import annotations

import hashlib
import logging
from typing import Never, cast
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.route_security import ResourceRequirement, SelectorRequirement, route_policy
from charteros.application.resource_limits import AbuseBudget
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.repositories.abuse import SqlAlchemyRateBudgetRepository
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow
from charteros.security.auth import (
    AuthenticatedPrincipal,
    AuthenticationBackend,
    AuthenticationError,
    HttpJwksSource,
    JwksKeyCache,
    OidcJwtAuthenticationBackend,
    Permission,
    PrincipalType,
    RejectingAuthenticationBackend,
)
from charteros.shared.config import Settings

logger = logging.getLogger(__name__)


def _uuid_header(request: Request, name: str) -> UUID | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden") from exc


def _has_tenant_admin(principal: AuthenticatedPrincipal) -> bool:
    return principal.principal_type is PrincipalType.ADMINISTRATOR and principal.has(
        Permission.TENANT_ADMIN
    )


def _authorize_selectors(
    *, request: Request, principal: AuthenticatedPrincipal, requirement: SelectorRequirement
) -> None:
    buyer_id = _uuid_header(request, "X-Buyer-Id")
    operator_id = _uuid_header(request, "X-Operator-Id")
    tenant_admin = _has_tenant_admin(principal)

    if buyer_id is not None and not tenant_admin and buyer_id not in principal.buyer_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if operator_id is not None and not tenant_admin and operator_id not in principal.operator_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    if requirement is SelectorRequirement.NONE:
        return
    if requirement is SelectorRequirement.BUYER:
        if buyer_id is None and not tenant_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return
    if requirement is SelectorRequirement.OPERATOR:
        if operator_id is None and not tenant_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return

    if tenant_admin and buyer_id is None and operator_id is None:
        return
    if (buyer_id is None) == (operator_id is None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


def _path_uuid(request: Request, name: str) -> UUID:
    raw = request.path_params.get(name)
    try:
        return raw if isinstance(raw, UUID) else UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden") from exc


def _authorize_resource(
    *, request: Request, principal: AuthenticatedPrincipal, requirement: ResourceRequirement
) -> None:
    if requirement is ResourceRequirement.NONE or _has_tenant_admin(principal):
        return

    buyer_id = _uuid_header(request, "X-Buyer-Id")
    operator_id = _uuid_header(request, "X-Operator-Id")
    factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with factory() as session:
        if requirement is ResourceRequirement.MISSION_BUYER:
            if buyer_id is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            mission_id = _path_uuid(request, "mission_id")
            allowed = session.scalar(
                select(MissionRow.id).where(
                    MissionRow.id == mission_id,
                    MissionRow.buyer_id == buyer_id,
                )
            )
        elif requirement is ResourceRequirement.TENDER_BUYER:
            if buyer_id is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            tender_id = _path_uuid(request, "tender_id")
            allowed = session.scalar(
                select(TenderRow.id)
                .join(MissionRow, TenderRow.mission_id == MissionRow.id)
                .where(TenderRow.id == tender_id, MissionRow.buyer_id == buyer_id)
            )
        elif requirement is ResourceRequirement.INVITATION_OPERATOR:
            if operator_id is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            invitation_id = _path_uuid(request, "invitation_id")
            allowed = session.scalar(
                select(TenderInvitationRow.id).where(
                    TenderInvitationRow.id == invitation_id,
                    TenderInvitationRow.operator_id == operator_id,
                )
            )
        else:
            if operator_id is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            invitation_header_id = _uuid_header(request, "X-Tender-Invitation-Id")
            if invitation_header_id is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            tender_id = _path_uuid(request, "tender_id")
            allowed = session.scalar(
                select(TenderInvitationRow.id).where(
                    TenderInvitationRow.id == invitation_header_id,
                    TenderInvitationRow.tender_id == tender_id,
                    TenderInvitationRow.operator_id == operator_id,
                )
            )

    if allowed is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


def _budget_limit(settings: Settings, budget: AbuseBudget) -> int:
    if budget is AbuseBudget.MATCHING:
        return settings.api_matching_requests_per_window
    if budget is AbuseBudget.REPOSITIONING:
        return settings.api_repositioning_requests_per_window
    if budget is AbuseBudget.EVIDENCE:
        return settings.api_evidence_requests_per_window
    return settings.api_graph_requests_per_window


def _budget_identity_digest(
    request: Request,
    principal: AuthenticatedPrincipal,
) -> str:
    buyer_id = request.headers.get("X-Buyer-Id", "")
    operator_id = request.headers.get("X-Operator-Id", "")
    material = "\x00".join(
        (
            principal.issuer,
            principal.subject,
            principal.principal_type.value,
            buyer_id,
            operator_id,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _enforce_abuse_budget(
    *,
    request: Request,
    principal: AuthenticatedPrincipal,
    budget: AbuseBudget | None,
) -> None:
    if budget is None:
        return
    settings = cast(Settings, request.app.state.settings)
    factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with factory.begin() as session:
        decision = SqlAlchemyRateBudgetRepository(session).consume(
            budget=budget,
            identity_digest=_budget_identity_digest(request, principal),
            limit=_budget_limit(settings, budget),
            window_seconds=settings.api_rate_limit_window_seconds,
        )
    if decision.allowed:
        return
    logger.warning(
        "api_resource_budget_exhausted",
        extra={
            "event": "api_resource_budget_exhausted",
            "budget": budget.value,
            "principal_type": principal.principal_type.value,
            "method": request.method,
            "path": request.url.path,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="resource_limit_exceeded",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _deny(
    *, request: Request, principal: AuthenticatedPrincipal | None, reason: str, code: int
) -> Never:
    logger.warning(
        "authorization_denied",
        extra={
            "event": "authorization_denied",
            "subject": principal.subject if principal is not None else None,
            "principal_type": (principal.principal_type.value if principal is not None else None),
            "method": request.method,
            "path": request.url.path,
            "reason": reason,
        },
    )
    headers = {"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None
    raise HTTPException(
        status_code=code,
        detail="authentication required" if code == status.HTTP_401_UNAUTHORIZED else "forbidden",
        headers=headers,
    )


def authorize_request(request: Request) -> None:
    route = request.scope.get("route")
    path_template = getattr(route, "path", None)
    if not isinstance(path_template, str):
        _deny(
            request=request,
            principal=None,
            reason="unresolved-route-template",
            code=status.HTTP_403_FORBIDDEN,
        )
    policy = route_policy(request.method, path_template)
    if policy is None:
        logger.error(
            "route_security_policy_missing",
            extra={
                "event": "route_security_policy_missing",
                "method": request.method,
                "path_template": path_template,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="route security policy is not configured",
        )

    backend = cast(AuthenticationBackend, request.app.state.auth_backend)
    try:
        principal = backend.authenticate(request.headers.get("Authorization"))
    except AuthenticationError:
        _deny(
            request=request,
            principal=None,
            reason="authentication-failed",
            code=status.HTTP_401_UNAUTHORIZED,
        )

    if principal.principal_type not in policy.allowed_principal_types:
        _deny(
            request=request,
            principal=principal,
            reason="principal-type",
            code=status.HTTP_403_FORBIDDEN,
        )
    if not principal.has(policy.permission):
        _deny(
            request=request,
            principal=principal,
            reason="missing-permission",
            code=status.HTTP_403_FORBIDDEN,
        )
    try:
        _authorize_selectors(
            request=request,
            principal=principal,
            requirement=policy.selector,
        )
        _authorize_resource(
            request=request,
            principal=principal,
            requirement=policy.resource,
        )
    except HTTPException:
        _deny(
            request=request,
            principal=principal,
            reason="selector-or-resource-scope",
            code=status.HTTP_403_FORBIDDEN,
        )

    request.state.authenticated_principal = principal
    _enforce_abuse_budget(
        request=request,
        principal=principal,
        budget=policy.abuse_budget,
    )


def build_auth_backend(settings: Settings) -> AuthenticationBackend:
    issuer = settings.auth_issuer
    audience = settings.auth_audience
    jwks_url = settings.auth_jwks_url
    if issuer is None or audience is None or jwks_url is None:
        return RejectingAuthenticationBackend()

    algorithms = tuple(
        item.strip() for item in settings.auth_allowed_algorithms.split(",") if item.strip()
    )
    source = HttpJwksSource(
        url=jwks_url,
        timeout_seconds=settings.auth_http_timeout_seconds,
    )
    key_cache = JwksKeyCache(
        source=source,
        ttl_seconds=settings.auth_jwks_cache_ttl_seconds,
        max_keys=settings.auth_jwks_max_keys,
        refresh_min_interval_seconds=settings.auth_jwks_refresh_min_interval_seconds,
        unknown_key_ttl_seconds=settings.auth_jwks_unknown_key_ttl_seconds,
        max_negative_keys=settings.auth_jwks_negative_cache_max_keys,
    )
    return OidcJwtAuthenticationBackend(
        issuer=issuer,
        audience=audience,
        allowed_algorithms=algorithms,
        key_cache=key_cache,
        leeway_seconds=settings.auth_jwt_leeway_seconds,
    )
