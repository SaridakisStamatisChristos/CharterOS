from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode

import charteros.security.auth as auth_module
from charteros.security.auth import (
    AuthenticationError,
    HttpJwksSource,
    JwksKeyCache,
    OidcJwtAuthenticationBackend,
    Permission,
    PrincipalType,
)

ISSUER = "https://id.example.test/"
AUDIENCE = "charteros-api"
BUYER_ID = UUID("11111111-1111-1111-1111-111111111111")


class _MutableJwksSource:
    def __init__(self, keys: list[dict[str, object]]) -> None:
        self.keys = keys
        self.fail = False
        self.fetch_count = 0

    def fetch(self) -> Mapping[str, object]:
        self.fetch_count += 1
        if self.fail:
            raise AuthenticationError("JWKS unavailable")
        return {"keys": self.keys}


def _b64_int(value: int) -> str:
    width = max(1, (value.bit_length() + 7) // 8)
    return base64url_encode(value.to_bytes(width, "big")).decode("ascii")


def _jwk(key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, object]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64_int(numbers.n),
        "e": _b64_int(numbers.e),
    }


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "buyer-user-1",
        "iat": int(now.timestamp()),
        "nbf": int((now - timedelta(seconds=1)).timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "charteros_principal_type": PrincipalType.BUYER.value,
        "charteros_permissions": [Permission.BUYER_MISSION_READ.value],
        "charteros_buyer_ids": [str(BUYER_ID)],
        "charteros_operator_ids": [],
    }
    claims.update(overrides)
    return claims


def _token(
    key: rsa.RSAPrivateKey,
    *,
    kid: str = "key-1",
    claims: dict[str, Any] | None = None,
) -> str:
    return jwt.encode(
        claims or _claims(),
        key,
        algorithm="RS256",
        headers={"kid": kid, "typ": "JWT"},
    )


def _backend(
    source: _MutableJwksSource,
    *,
    ttl_seconds: float = 300,
    refresh_min_interval_seconds: float = 5,
    unknown_key_ttl_seconds: float = 5,
    monotonic: Callable[[], float] | None = None,
) -> OidcJwtAuthenticationBackend:
    cache = JwksKeyCache(
        source=source,
        ttl_seconds=ttl_seconds,
        refresh_min_interval_seconds=refresh_min_interval_seconds,
        unknown_key_ttl_seconds=unknown_key_ttl_seconds,
        monotonic=monotonic or time.monotonic,
    )
    return OidcJwtAuthenticationBackend(
        issuer=ISSUER,
        audience=AUDIENCE,
        allowed_algorithms=("RS256",),
        key_cache=cache,
    )


def test_valid_asymmetric_token_constructs_typed_principal() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(key, kid="key-1")])

    principal = _backend(source).authenticate(f"Bearer {_token(key)}")

    assert principal.subject == "buyer-user-1"
    assert principal.principal_type is PrincipalType.BUYER
    assert principal.buyer_ids == frozenset({BUYER_ID})
    assert principal.operator_ids == frozenset()
    assert principal.permissions == frozenset({Permission.BUYER_MISSION_READ})
    assert principal.issuer == ISSUER
    assert principal.key_id == "key-1"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer one two"])
def test_missing_or_malformed_authorization_header_fails_closed(header: str | None) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(key, kid="key-1")]))

    with pytest.raises(AuthenticationError):
        backend.authenticate(header)


def test_unsigned_token_is_rejected_before_key_use() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(key, kid="key-1")]))
    unsigned = jwt.encode(_claims(), key="", algorithm="none", headers={"kid": "key-1"})

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {unsigned}")


def test_wrong_signature_is_rejected() -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(trusted, kid="key-1")]))

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {_token(attacker)}")


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.invalid/"},
        {"aud": "other-api"},
        {"exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())},
        {"nbf": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())},
    ],
)
def test_standard_claim_validation_fails_closed(overrides: dict[str, Any]) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(key, kid="key-1")]))

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {_token(key, claims=_claims(**overrides))}")


def test_unknown_key_id_is_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(key, kid="key-1")]))

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {_token(key, kid='unknown')}")


def test_same_kid_key_rotation_forces_refresh_after_signature_failure() -> None:
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(old_key, kid="key-1")])
    backend = _backend(source)

    backend.authenticate(f"Bearer {_token(old_key)}")
    source.keys = [_jwk(new_key, kid="key-1")]

    principal = backend.authenticate(f"Bearer {_token(new_key)}")

    assert principal.subject == "buyer-user-1"
    assert source.fetch_count == 2


def test_jwks_failure_is_fail_closed_after_cached_key_expires() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(key, kid="key-1")])
    now = [0.0]
    backend = _backend(source, ttl_seconds=1, monotonic=lambda: now[0])
    token = _token(key)
    backend.authenticate(f"Bearer {token}")

    source.fail = True
    now[0] = 2.0

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {token}")


def test_untrusted_signing_key_cannot_assert_privileged_permission() -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    backend = _backend(_MutableJwksSource([_jwk(trusted, kid="key-1")]))
    forged_claims = _claims(
        charteros_principal_type=PrincipalType.ADMINISTRATOR.value,
        charteros_permissions=[Permission.FX_RATE_WRITE.value],
    )

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {_token(attacker, claims=forged_claims)}")


def test_jwks_cache_rejects_unbounded_key_sets() -> None:
    keys = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3)]
    source = _MutableJwksSource([_jwk(key, kid=f"key-{index}") for index, key in enumerate(keys)])
    cache = JwksKeyCache(source=source, ttl_seconds=300, max_keys=2)

    with pytest.raises(AuthenticationError):
        cache.key_for(key_id="key-0")


def test_symmetric_algorithm_configuration_is_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cache = JwksKeyCache(
        source=_MutableJwksSource([_jwk(key, kid="key-1")]),
        ttl_seconds=300,
    )

    with pytest.raises(ValueError):
        OidcJwtAuthenticationBackend(
            issuer=ISSUER,
            audience=AUDIENCE,
            allowed_algorithms=("HS256",),
            key_cache=cache,
        )


def test_unknown_kid_storm_from_cold_cache_is_single_refresh_per_interval() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(key, kid="key-1")])
    now = [0.0]
    backend = _backend(
        source,
        monotonic=lambda: now[0],
        refresh_min_interval_seconds=5,
        unknown_key_ttl_seconds=5,
    )

    for index in range(20):
        with pytest.raises(AuthenticationError):
            backend.authenticate(f"Bearer {_token(key, kid=f'cold-unknown-{index}')}")

    assert source.fetch_count == 1


def test_unknown_kid_storm_triggers_at_most_one_refresh_per_interval() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(key, kid="key-1")])
    now = [0.0]
    backend = _backend(
        source,
        monotonic=lambda: now[0],
        refresh_min_interval_seconds=5,
        unknown_key_ttl_seconds=5,
    )
    backend.authenticate(f"Bearer {_token(key)}")

    for index in range(20):
        with pytest.raises(AuthenticationError):
            backend.authenticate(f"Bearer {_token(key, kid=f'unknown-{index}')}")

    assert source.fetch_count == 2


def test_new_kid_rotation_is_visible_after_bounded_negative_cache_window() -> None:
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(old_key, kid="key-1")])
    now = [0.0]
    backend = _backend(
        source,
        monotonic=lambda: now[0],
        refresh_min_interval_seconds=5,
        unknown_key_ttl_seconds=5,
    )
    backend.authenticate(f"Bearer {_token(old_key)}")

    with pytest.raises(AuthenticationError):
        backend.authenticate(f"Bearer {_token(new_key, kid='key-2')}")
    source.keys = [_jwk(old_key, kid="key-1"), _jwk(new_key, kid="key-2")]
    now[0] = 6.0

    principal = backend.authenticate(f"Bearer {_token(new_key, kid='key-2')}")

    assert principal.subject == "buyer-user-1"
    assert source.fetch_count == 3


def test_invalid_signature_storm_is_refresh_throttled() -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = _MutableJwksSource([_jwk(trusted, kid="key-1")])
    now = [0.0]
    backend = _backend(
        source,
        monotonic=lambda: now[0],
        refresh_min_interval_seconds=5,
    )
    backend.authenticate(f"Bearer {_token(trusted)}")

    for _ in range(10):
        with pytest.raises(AuthenticationError):
            backend.authenticate(f"Bearer {_token(attacker)}")

    assert source.fetch_count == 2


def test_duplicate_jwks_key_ids_fail_closed() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    duplicate = _jwk(key, kid="duplicate")
    cache = JwksKeyCache(
        source=_MutableJwksSource([duplicate, duplicate]),
        ttl_seconds=300,
    )

    with pytest.raises(AuthenticationError, match="duplicate key identifier"):
        cache.key_for(key_id="duplicate")


def test_malformed_jwks_without_usable_keys_fails_closed() -> None:
    source = _MutableJwksSource([{"kid": "broken"}])
    cache = JwksKeyCache(source=source, ttl_seconds=300)

    with pytest.raises(AuthenticationError, match="no usable signing keys"):
        cache.key_for(key_id="broken")


def test_http_jwks_source_rejects_oversized_document_before_json_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return b"{" + (b"x" * 64) + b"}"

    def fake_urlopen(_request: object, *, timeout: float) -> _Response:
        del timeout
        return _Response()

    monkeypatch.setattr(auth_module, "urlopen", fake_urlopen)
    source = HttpJwksSource(
        url="https://id.example.test/.well-known/jwks.json",
        timeout_seconds=1,
        max_document_bytes=16,
    )

    with pytest.raises(AuthenticationError, match="exceeds the size bound"):
        source.fetch()
