from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

import jwt
from jwt import InvalidSignatureError, InvalidTokenError, PyJWK
from jwt.exceptions import PyJWKError


class PrincipalType(StrEnum):
    BUYER = "buyer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"
    SERVICE = "service"


class Permission(StrEnum):
    CATALOG_WRITE = "catalog:write"
    FLEET_READ = "fleet:read"
    FLEET_WRITE = "fleet:write"
    FX_RATE_READ = "fx-rate:read"
    FX_RATE_WRITE = "fx-rate:write"
    DOMAIN_READ = "domain:read"
    DOMAIN_WRITE = "domain:write"
    GRAPH_READ = "graph:read"
    OPTIMIZATION_READ = "optimization:read"
    TENDER_BUYER_READ = "tender:buyer-read"
    TENDER_BUYER_WRITE = "tender:buyer-write"
    TENDER_OPERATOR_WRITE = "tender:operator-write"
    TENDER_ADMIN_CORRECT = "tender:admin-correct"
    TENDER_SUPPLIER_READ = "tender:supplier-read"
    BUYER_MISSION_READ = "buyer:mission-read"
    BUYER_MISSION_WRITE = "buyer:mission-write"
    BUYER_PROCUREMENT_APPROVE = "buyer:procurement-approve"
    BUYER_FX_LOCK_CREATE = "buyer:fx-lock-create"
    OPERATOR_FLEET_READ = "operator:fleet-read"
    OPERATOR_FLEET_WRITE = "operator:fleet-write"
    OPERATOR_RFQ_READ = "operator:rfq-read"
    OPERATOR_RFQ_WRITE = "operator:rfq-write"
    OPERATOR_QUOTE_READ = "operator:quote-read"
    OPERATOR_QUOTE_WRITE = "operator:quote-write"
    OPERATOR_BOOKING_READ = "operator:booking-read"
    DISRUPTION_READ = "disruption:read"
    DISRUPTION_WRITE = "disruption:write"
    RECONCILIATION_READ = "reconciliation:read"
    RECONCILIATION_WRITE = "reconciliation:write"
    AUDIT_EVIDENCE_READ = "audit-evidence:read"
    TENANT_ADMIN = "tenant:admin"
    SERVICE_OUTBOX_WORK = "service:outbox-work"
    SERVICE_GRAPH_PROJECT = "service:graph-project"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    subject: str
    principal_type: PrincipalType
    permissions: frozenset[Permission]
    buyer_ids: frozenset[UUID]
    operator_ids: frozenset[UUID]
    issuer: str
    key_id: str

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions


class AuthenticationError(RuntimeError):
    """Fail-closed authentication failure safe to collapse into HTTP 401."""


class AuthenticationBackend(Protocol):
    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal: ...


class JwksSource(Protocol):
    def fetch(self) -> Mapping[str, object]: ...


class HttpJwksSource:
    def __init__(self, *, url: str, timeout_seconds: float) -> None:
        if not url.startswith("https://"):
            raise ValueError("JWKS URL must use HTTPS")
        self._url = url
        self._timeout_seconds = timeout_seconds

    def fetch(self) -> Mapping[str, object]:
        request = Request(
            self._url,
            headers={"Accept": "application/json", "User-Agent": "CharterOS/oidc-jwks"},
            method="GET",
        )
        try:
            # HttpJwksSource.__init__ rejects every non-HTTPS URL before this request is built.
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec B310
                if response.status != 200:
                    raise AuthenticationError("identity key endpoint returned a non-success status")
                payload = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise AuthenticationError("identity key endpoint is unavailable") from exc
        if not isinstance(payload, dict):
            raise AuthenticationError("identity key endpoint returned an invalid document")
        return cast(Mapping[str, object], payload)


@dataclass(frozen=True, slots=True)
class _KeySet:
    keys: Mapping[str, PyJWK]
    expires_at: float


class JwksKeyCache:
    """Bounded-TTL JWKS cache with synchronous forced refresh for unknown/rotated keys."""

    def __init__(
        self,
        *,
        source: JwksSource,
        ttl_seconds: float,
        max_keys: int = 64,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("JWKS cache TTL must be positive")
        if max_keys <= 0:
            raise ValueError("JWKS cache key bound must be positive")
        self._source = source
        self._ttl_seconds = ttl_seconds
        self._max_keys = max_keys
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._key_set: _KeySet | None = None

    def key_for(self, *, key_id: str, force_refresh: bool = False) -> PyJWK:
        now = self._monotonic()
        with self._lock:
            current = self._key_set
            if not force_refresh and current is not None and now < current.expires_at:
                key = current.keys.get(key_id)
                if key is not None:
                    return key

            refreshed = self._refresh(now)
            key = refreshed.keys.get(key_id)
            if key is None:
                raise AuthenticationError("token signing key is unknown")
            return key

    def _refresh(self, now: float) -> _KeySet:
        document = self._source.fetch()
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list):
            raise AuthenticationError("JWKS document does not contain a keys array")
        if len(raw_keys) > self._max_keys:
            raise AuthenticationError("JWKS document exceeds the configured key bound")

        parsed: dict[str, PyJWK] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                continue
            key_id = raw_key.get("kid")
            if not isinstance(key_id, str) or not key_id:
                continue
            use = raw_key.get("use")
            if use is not None and use != "sig":
                continue
            if key_id in parsed:
                raise AuthenticationError("JWKS document contains a duplicate key identifier")
            try:
                parsed[key_id] = PyJWK.from_dict(cast(dict[str, Any], raw_key))
            except (InvalidTokenError, PyJWKError, ValueError, TypeError):
                continue

        if not parsed:
            raise AuthenticationError("JWKS document contains no usable signing keys")
        result = _KeySet(keys=parsed, expires_at=now + self._ttl_seconds)
        self._key_set = result
        return result


class OidcJwtAuthenticationBackend:
    """Provider-neutral asymmetric JWT verifier backed by an OIDC-compatible JWKS set."""

    _REQUIRED_CLAIMS = (
        "exp",
        "iat",
        "iss",
        "aud",
        "sub",
        "charteros_principal_type",
        "charteros_permissions",
    )

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        allowed_algorithms: Sequence[str],
        key_cache: JwksKeyCache,
        leeway_seconds: float = 0,
    ) -> None:
        if not issuer:
            raise ValueError("issuer is required")
        if not audience:
            raise ValueError("audience is required")
        allowed = tuple(dict.fromkeys(allowed_algorithms))
        if not allowed:
            raise ValueError("at least one JWT signing algorithm is required")
        if any(algorithm.lower() == "none" for algorithm in allowed):
            raise ValueError("unsigned JWTs are never allowed")
        if any(algorithm.upper().startswith("HS") for algorithm in allowed):
            raise ValueError("symmetric JWT algorithms are not accepted for OIDC verification")
        if leeway_seconds < 0:
            raise ValueError("JWT leeway cannot be negative")
        self._issuer = issuer
        self._audience = audience
        self._allowed_algorithms = allowed
        self._key_cache = key_cache
        self._leeway_seconds = leeway_seconds

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        token = _extract_bearer_token(authorization_header)
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise AuthenticationError("bearer token is malformed") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if not isinstance(algorithm, str) or algorithm not in self._allowed_algorithms:
            raise AuthenticationError("bearer token uses a disallowed signing algorithm")
        if not isinstance(key_id, str) or not key_id:
            raise AuthenticationError("bearer token is missing a signing key identifier")

        key = self._key_cache.key_for(key_id=key_id)
        if key.algorithm_name != algorithm:
            raise AuthenticationError("signing key algorithm does not match token algorithm")

        try:
            claims = self._decode(token=token, key=key)
        except InvalidSignatureError:
            rotated_key = self._key_cache.key_for(key_id=key_id, force_refresh=True)
            if rotated_key.algorithm_name != algorithm:
                raise AuthenticationError("rotated signing key algorithm mismatch") from None
            try:
                claims = self._decode(token=token, key=rotated_key)
            except InvalidTokenError as exc:
                raise AuthenticationError("bearer token validation failed") from exc
        except InvalidTokenError as exc:
            raise AuthenticationError("bearer token validation failed") from exc

        return _principal_from_claims(claims=claims, expected_issuer=self._issuer, key_id=key_id)

    def _decode(self, *, token: str, key: PyJWK) -> dict[str, Any]:
        return jwt.decode(
            token,
            key=key,
            algorithms=list(self._allowed_algorithms),
            audience=self._audience,
            issuer=self._issuer,
            leeway=self._leeway_seconds,
            options={
                "require": list(self._REQUIRED_CLAIMS),
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_sub": True,
            },
        )


class RejectingAuthenticationBackend:
    """Safe default when external identity verification is not configured."""

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        del authorization_header
        raise AuthenticationError("authentication is not configured")


def _extract_bearer_token(authorization_header: str | None) -> str:
    if authorization_header is None:
        raise AuthenticationError("bearer authentication is required")
    scheme, separator, token = authorization_header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or " " in token:
        raise AuthenticationError("authorization header must contain one bearer token")
    if len(token) > 16_384:
        raise AuthenticationError("bearer token exceeds the accepted size bound")
    return token


def _uuid_set(claims: Mapping[str, Any], key: str) -> frozenset[UUID]:
    raw = claims.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise AuthenticationError(f"{key} must be an array of UUID strings")
    try:
        return frozenset(UUID(item) for item in raw)
    except ValueError as exc:
        raise AuthenticationError(f"{key} contains an invalid UUID") from exc


def _principal_from_claims(
    *, claims: Mapping[str, Any], expected_issuer: str, key_id: str
) -> AuthenticatedPrincipal:
    subject = claims.get("sub")
    issuer = claims.get("iss")
    raw_principal_type = claims.get("charteros_principal_type")
    raw_permissions = claims.get("charteros_permissions")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("subject claim is invalid")
    if issuer != expected_issuer:
        raise AuthenticationError("issuer claim is invalid")
    if not isinstance(raw_principal_type, str):
        raise AuthenticationError("principal type claim is invalid")
    try:
        principal_type = PrincipalType(raw_principal_type)
    except ValueError as exc:
        raise AuthenticationError("principal type is not recognized") from exc
    if not isinstance(raw_permissions, list) or not all(
        isinstance(item, str) for item in raw_permissions
    ):
        raise AuthenticationError("permissions claim must be an array of strings")
    try:
        permissions = frozenset(Permission(item) for item in raw_permissions)
    except ValueError as exc:
        raise AuthenticationError("permissions claim contains an unknown capability") from exc

    buyer_ids = _uuid_set(claims, "charteros_buyer_ids")
    operator_ids = _uuid_set(claims, "charteros_operator_ids")
    if principal_type is PrincipalType.BUYER and not buyer_ids:
        raise AuthenticationError("buyer principal has no buyer organization membership")
    if principal_type is PrincipalType.OPERATOR and not operator_ids:
        raise AuthenticationError("operator principal has no operator membership")

    return AuthenticatedPrincipal(
        subject=subject,
        principal_type=principal_type,
        permissions=permissions,
        buyer_ids=buyer_ids,
        operator_ids=operator_ids,
        issuer=expected_issuer,
        key_id=key_id,
    )
