from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from cryptography import x509
from jwt import PyJWK

from charteros.security.auth import Permission
from tools.dev_identity import _ALL_PERMISSIONS, init_identity, mint_token, shell_environment


def test_evaluation_permission_profile_tracks_application_permissions() -> None:
    assert set(_ALL_PERMISSIONS) == {permission.value for permission in Permission}


def test_init_identity_creates_tls_and_jwt_material(tmp_path: Path) -> None:
    directory = tmp_path / "identity"

    metadata = init_identity(directory)

    assert metadata.issuer == "https://127.0.0.1:8765/"
    assert metadata.jwks_url == "https://127.0.0.1:8765/.well-known/jwks.json"
    assert (directory / "ca-cert.pem").is_file()
    assert (directory / "ca-bundle.pem").is_file()
    assert (directory / "tls-cert.pem").is_file()
    assert (directory / "tls-key.pem").is_file()
    assert (directory / "jwt-signing-key.pem").is_file()
    assert (directory / "jwks.json").is_file()

    certificate = x509.load_pem_x509_certificate((directory / "tls-cert.pem").read_bytes())
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "127.0.0.1" in [str(value) for value in san.get_values_for_type(x509.IPAddress)]

    if os.name == "posix":
        assert (directory / "tls-key.pem").stat().st_mode & 0o077 == 0
        assert (directory / "jwt-signing-key.pem").stat().st_mode & 0o077 == 0


def test_minted_admin_token_verifies_against_generated_jwks(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    metadata = init_identity(directory)

    token = mint_token(directory, ttl_seconds=60)
    jwks = json.loads((directory / "jwks.json").read_text(encoding="utf-8"))
    key = PyJWK.from_dict(jwks["keys"][0])
    claims = jwt.decode(
        token,
        key=key,
        algorithms=["RS256"],
        issuer=metadata.issuer,
        audience=metadata.audience,
    )

    assert claims["charteros_principal_type"] == "administrator"
    assert "tenant:admin" in claims["charteros_permissions"]
    assert claims["charteros_buyer_ids"] == []
    assert claims["charteros_operator_ids"] == []


def test_buyer_token_requires_membership(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    init_identity(directory)

    with pytest.raises(SystemExit, match="buyer tokens require"):
        mint_token(directory, principal_type="buyer")

    buyer_id = UUID("11111111-1111-1111-1111-111111111111")
    token = mint_token(directory, principal_type="buyer", buyer_ids=(buyer_id,))
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert unverified["charteros_buyer_ids"] == [str(buyer_id)]


def test_shell_environment_uses_https_jwks_and_local_ca(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    init_identity(directory, host="localhost", port=9443, audience="example-api")

    exports = shell_environment(directory)

    assert "CHARTEROS_AUTH_ISSUER=https://localhost:9443/" in exports
    assert "CHARTEROS_AUTH_AUDIENCE=example-api" in exports
    assert "CHARTEROS_AUTH_JWKS_URL=https://localhost:9443/.well-known/jwks.json" in exports
    assert "CHARTEROS_AUTH_ALLOWED_ALGORITHMS=RS256" in exports
    assert "SSL_CERT_FILE=" in exports


def test_non_loopback_identity_host_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="loopback"):
        init_identity(tmp_path / "identity", host="0.0.0.0")


def test_generated_ca_can_validate_local_server_certificate(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    init_identity(directory, host="localhost")

    context = ssl.create_default_context(cafile=str(directory / "ca-cert.pem"))

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
