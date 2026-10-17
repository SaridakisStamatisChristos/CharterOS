from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shlex
import ssl
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from jwt.utils import base64url_encode

DEFAULT_DIRECTORY = Path(".charteros-dev/identity")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_AUDIENCE = "charteros-api"
DEFAULT_KEY_ID = "charteros-dev-rs256"
MAX_TOKEN_TTL_SECONDS = 3600

_CA_CERT = "ca-cert.pem"
_CA_BUNDLE = "ca-bundle.pem"
_TLS_CERT = "tls-cert.pem"
_TLS_KEY = "tls-key.pem"
_JWT_KEY = "jwt-signing-key.pem"
_JWKS = "jwks.json"
_METADATA = "metadata.json"
_REQUIRED_FILES = (_CA_CERT, _CA_BUNDLE, _TLS_CERT, _TLS_KEY, _JWT_KEY, _JWKS, _METADATA)

# Keep this local helper independent from application imports so developers can create the
# evaluation identity before the application configuration is loaded.
_ALL_PERMISSIONS = (
    "catalog:write",
    "fleet:read",
    "fleet:write",
    "fx-rate:read",
    "fx-rate:write",
    "domain:read",
    "domain:write",
    "graph:read",
    "optimization:read",
    "tender:buyer-read",
    "tender:buyer-write",
    "tender:operator-write",
    "tender:admin-correct",
    "tender:supplier-read",
    "buyer:mission-read",
    "buyer:mission-write",
    "buyer:procurement-approve",
    "buyer:fx-lock-create",
    "operator:fleet-read",
    "operator:fleet-write",
    "operator:rfq-read",
    "operator:rfq-write",
    "operator:quote-read",
    "operator:quote-write",
    "operator:booking-read",
    "disruption:read",
    "disruption:write",
    "reconciliation:read",
    "reconciliation:write",
    "audit-evidence:read",
    "tenant:admin",
    "tenant:governance-read",
    "tenant:governance-write",
    "tenant:data-export",
    "service:outbox-work",
    "service:graph-project",
)
_PRINCIPAL_TYPES = ("buyer", "operator", "administrator", "service")


@dataclass(frozen=True, slots=True)
class IdentityMetadata:
    host: str
    port: int
    issuer: str
    audience: str
    jwks_url: str
    key_id: str


def _die(message: str) -> NoReturn:
    raise SystemExit(message)


def _b64_int(value: int) -> str:
    width = max(1, (value.bit_length() + 7) // 8)
    return base64url_encode(value.to_bytes(width, "big")).decode("ascii")


def _private_key_bytes(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(payload)
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _server_san(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _validate_host(host: str) -> None:
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        _die("host must be localhost or a loopback IPv4 address")
    if address.version != 4 or not address.is_loopback:
        _die("host must be localhost or a loopback IPv4 address")


def _issuer(host: str, port: int) -> str:
    return f"https://{host}:{port}/"


def _load_metadata(directory: Path) -> IdentityMetadata:
    try:
        raw = json.loads((directory / _METADATA).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("metadata is not an object")
        return IdentityMetadata(
            host=str(raw["host"]),
            port=int(raw["port"]),
            issuer=str(raw["issuer"]),
            audience=str(raw["audience"]),
            jwks_url=str(raw["jwks_url"]),
            key_id=str(raw["key_id"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        _die(f"invalid local identity metadata in {directory}: {exc}")


def init_identity(
    directory: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    audience: str = DEFAULT_AUDIENCE,
    force: bool = False,
) -> IdentityMetadata:
    if not 1 <= port <= 65535:
        _die("port must be between 1 and 65535")
    _validate_host(host)
    if not audience:
        _die("audience must not be empty")

    existing = [directory / name for name in _REQUIRED_FILES if (directory / name).exists()]
    if existing and not force:
        if len(existing) == len(_REQUIRED_FILES):
            metadata = _load_metadata(directory)
            if (metadata.host, metadata.port, metadata.audience) == (host, port, audience):
                return metadata
        _die(f"identity directory {directory} already exists; use --force to rotate it")

    now = datetime.now(UTC)
    not_before = now - timedelta(minutes=1)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CharterOS local evaluation CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tls_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    tls_cert = (
        x509.CertificateBuilder()
        .subject_name(tls_name)
        .issuer_name(ca_name)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(tls_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName([_server_san(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    jwt_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = jwt_key.public_key().public_numbers()
    jwk: dict[str, object] = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": DEFAULT_KEY_ID,
        "n": _b64_int(public_numbers.n),
        "e": _b64_int(public_numbers.e),
    }
    issuer = _issuer(host, port)
    metadata = IdentityMetadata(
        host=host,
        port=port,
        issuer=issuer,
        audience=audience,
        jwks_url=f"{issuer}.well-known/jwks.json",
        key_id=DEFAULT_KEY_ID,
    )

    directory.mkdir(parents=True, exist_ok=True)
    ca_payload = ca_cert.public_bytes(serialization.Encoding.PEM)
    _atomic_write(directory / _CA_CERT, ca_payload, mode=0o644)
    default_ca_file = ssl.get_default_verify_paths().cafile
    system_ca_payload = b""
    if default_ca_file is not None:
        try:
            system_ca_payload = Path(default_ca_file).read_bytes()
        except OSError:
            system_ca_payload = b""
    separator = b"" if not system_ca_payload or system_ca_payload.endswith(b"\n") else b"\n"
    _atomic_write(
        directory / _CA_BUNDLE,
        system_ca_payload + separator + ca_payload,
        mode=0o644,
    )
    _atomic_write(
        directory / _TLS_CERT,
        tls_cert.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
    )
    _atomic_write(directory / _TLS_KEY, _private_key_bytes(tls_key), mode=0o600)
    _atomic_write(directory / _JWT_KEY, _private_key_bytes(jwt_key), mode=0o600)
    _atomic_write(
        directory / _JWKS,
        (json.dumps({"keys": [jwk]}, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o644,
    )
    _atomic_write(
        directory / _METADATA,
        (json.dumps(asdict(metadata), indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o644,
    )
    return metadata


def shell_environment(directory: Path) -> str:
    metadata = _load_metadata(directory)
    values = {
        "SSL_CERT_FILE": str((directory / _CA_BUNDLE).resolve()),
        "CHARTEROS_AUTH_ISSUER": metadata.issuer,
        "CHARTEROS_AUTH_AUDIENCE": metadata.audience,
        "CHARTEROS_AUTH_JWKS_URL": metadata.jwks_url,
        "CHARTEROS_AUTH_ALLOWED_ALGORITHMS": "RS256",
    }
    return "\n".join(f"export {name}={shlex.quote(value)}" for name, value in values.items())


def mint_token(
    directory: Path,
    *,
    principal_type: str = "administrator",
    subject: str = "charteros-local-evaluator",
    buyer_ids: tuple[UUID, ...] = (),
    operator_ids: tuple[UUID, ...] = (),
    ttl_seconds: int = 900,
) -> str:
    if principal_type not in _PRINCIPAL_TYPES:
        _die(f"principal type must be one of: {', '.join(_PRINCIPAL_TYPES)}")
    if not 60 <= ttl_seconds <= MAX_TOKEN_TTL_SECONDS:
        _die(f"token TTL must be between 60 and {MAX_TOKEN_TTL_SECONDS} seconds")
    if principal_type == "buyer" and not buyer_ids:
        _die("buyer tokens require at least one --buyer-id")
    if principal_type == "operator" and not operator_ids:
        _die("operator tokens require at least one --operator-id")

    metadata = _load_metadata(directory)
    try:
        key = serialization.load_pem_private_key((directory / _JWT_KEY).read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        _die(f"cannot load local JWT signing key: {exc}")
    if not isinstance(key, rsa.RSAPrivateKey):
        _die("local JWT signing key is not RSA")

    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": metadata.issuer,
        "aud": metadata.audience,
        "sub": subject,
        "iat": int(now.timestamp()),
        "nbf": int((now - timedelta(seconds=1)).timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "charteros_principal_type": principal_type,
        "charteros_permissions": list(_ALL_PERMISSIONS),
        "charteros_buyer_ids": [str(item) for item in buyer_ids],
        "charteros_operator_ids": [str(item) for item in operator_ids],
    }
    return jwt.encode(
        claims,
        key,
        algorithm="RS256",
        headers={"kid": metadata.key_id, "typ": "JWT"},
    )


class _IdentityRequestHandler(BaseHTTPRequestHandler):
    server_version = "CharterOSDevIdentity/1"

    def do_GET(self) -> None:
        server = self.server
        if not isinstance(server, _IdentityServer):
            self.send_error(500)
            return
        if self.path == "/health":
            self._json_response({"status": "ok"})
            return
        if self.path == "/.well-known/jwks.json":
            self._json_response(server.jwks_document)
            return
        if self.path == "/.well-known/openid-configuration":
            self._json_response(
                {
                    "issuer": server.metadata.issuer,
                    "jwks_uri": server.metadata.jwks_url,
                    "id_token_signing_alg_values_supported": ["RS256"],
                }
            )
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("dev-identity: " + format % args + "\n")

    def _json_response(self, document: object) -> None:
        payload = (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


class _IdentityServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, directory: Path) -> None:
        self.metadata = _load_metadata(directory)
        raw_jwks = json.loads((directory / _JWKS).read_text(encoding="utf-8"))
        if not isinstance(raw_jwks, dict):
            _die("local JWKS document is invalid")
        self.jwks_document = raw_jwks
        super().__init__((self.metadata.host, self.metadata.port), _IdentityRequestHandler)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            certfile=directory / _TLS_CERT,
            keyfile=directory / _TLS_KEY,
        )
        self.socket = context.wrap_socket(self.socket, server_side=True)


def serve_identity(directory: Path) -> None:
    server = _IdentityServer(directory)
    try:
        print(f"CharterOS local identity: {server.metadata.issuer}", file=sys.stderr)
        print("Press Ctrl+C to stop.", file=sys.stderr)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local HTTPS OIDC/JWKS identity helper for CharterOS evaluation only."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help=f"identity state directory (default: {DEFAULT_DIRECTORY})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create local CA/TLS/JWT keys and JWKS")
    init_parser.add_argument("--host", default=DEFAULT_HOST)
    init_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    init_parser.add_argument("--audience", default=DEFAULT_AUDIENCE)
    init_parser.add_argument("--force", action="store_true", help="rotate existing local identity")

    subparsers.add_parser("env", help="print shell exports for CharterOS authentication")
    subparsers.add_parser("serve", help="serve JWKS/discovery over local HTTPS")

    token_parser = subparsers.add_parser("token", help="mint a short-lived local evaluation JWT")
    token_parser.add_argument("--principal-type", choices=_PRINCIPAL_TYPES, default="administrator")
    token_parser.add_argument("--subject", default="charteros-local-evaluator")
    token_parser.add_argument("--buyer-id", action="append", type=UUID, default=[])
    token_parser.add_argument("--operator-id", action="append", type=UUID, default=[])
    token_parser.add_argument("--ttl-seconds", type=int, default=900)
    return parser


def main() -> None:
    args = _parser().parse_args()
    directory: Path = args.directory
    if args.command == "init":
        metadata = init_identity(
            directory,
            host=args.host,
            port=args.port,
            audience=args.audience,
            force=args.force,
        )
        print(json.dumps(asdict(metadata), indent=2, sort_keys=True))
        return
    if args.command == "env":
        print(shell_environment(directory))
        return
    if args.command == "serve":
        serve_identity(directory)
        return
    if args.command == "token":
        print(
            mint_token(
                directory,
                principal_type=args.principal_type,
                subject=args.subject,
                buyer_ids=tuple(args.buyer_id),
                operator_ids=tuple(args.operator_id),
                ttl_seconds=args.ttl_seconds,
            )
        )
        return
    _die(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
