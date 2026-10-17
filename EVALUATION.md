# CharterOS Local Product Evaluation

This path is for evaluating the real CharterOS API locally without provisioning an external OIDC provider.
It does **not** add an authentication bypass. The API still validates an asymmetric RS256 JWT against an
HTTPS JWKS endpoint through the normal `HttpJwksSource` and `OidcJwtAuthenticationBackend` path.

The local identity helper:

- binds only to `localhost` or a loopback IPv4 address;
- generates a local CA, TLS certificate, and independent JWT signing key;
- serves only OIDC discovery, JWKS, and a health endpoint over HTTPS;
- mints short-lived tokens (15 minutes by default, one hour maximum);
- writes private material under `.charteros-dev/`, which is ignored by Git; and
- exports a process-local CA bundle so system HTTPS roots remain available.

Do not use this identity helper in staging or production. Those environments should use the real external
OIDC issuer described in the main deployment documentation.

## 1. Prepare CharterOS

Install dependencies, start PostgreSQL, and apply migrations using the normal project workflow:

```bash
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
```

If you use `.env`, keep the normal development `CHARTEROS_DATABASE_URL` from `.env.example` or provide your
own PostgreSQL URL.

## 2. Generate a local evaluation identity

```bash
uv run python tools/dev_identity.py init
```

The command is idempotent when its host, port, and audience match the existing identity. To deliberately
rotate all local evaluation credentials, use `--force`.

## 3. Start the local HTTPS JWKS service

In terminal A:

```bash
uv run python tools/dev_identity.py serve
```

The default issuer is `https://127.0.0.1:8765/`.

## 4. Start CharterOS with the generated OIDC configuration

In terminal B, load only the generated identity settings into that shell and start the API:

```bash
eval "$(uv run python tools/dev_identity.py env)"
uv run uvicorn apps.api.main:app --reload
```

`env` sets:

- `CHARTEROS_AUTH_ISSUER`
- `CHARTEROS_AUTH_AUDIENCE`
- `CHARTEROS_AUTH_JWKS_URL`
- `CHARTEROS_AUTH_ALLOWED_ALGORITHMS=RS256`
- `SSL_CERT_FILE` to a generated bundle containing the system CA roots plus the local evaluation CA

It does not modify global trust settings.

## 5. Exercise a protected product path

In terminal C, mint a short-lived administrator token:

```bash
TOKEN="$(uv run python tools/dev_identity.py token)"
```

The public health endpoint should respond normally:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

A protected catalog write without a token should fail closed with HTTP 401:

```bash
curl --include \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: evaluation-organization-unauthenticated' \
  --data '{"type":"buyer","legal_name":"CharterOS Evaluation Buyer","country":"GR"}' \
  http://127.0.0.1:8000/v1/organizations
```

The same real route with the generated administrator token should create an organization:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${TOKEN}" \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: evaluation-organization-1' \
  --data '{"type":"buyer","legal_name":"CharterOS Evaluation Buyer","country":"GR"}' \
  http://127.0.0.1:8000/v1/organizations
```

This verifies the product path through the real API, database transaction, idempotency handling, route
policy, HTTPS JWKS retrieval, asymmetric JWT verification, and authorization policy.

## Scoped buyer/operator tokens

Administrator tokens are convenient for initial product exploration. To exercise tenant scoping, mint a
buyer or operator token with the relevant persisted organization/operator UUID:

```bash
uv run python tools/dev_identity.py token \
  --principal-type buyer \
  --buyer-id 11111111-1111-1111-1111-111111111111

uv run python tools/dev_identity.py token \
  --principal-type operator \
  --operator-id 22222222-2222-2222-2222-222222222222
```

Buyer tokens require at least one `--buyer-id`; operator tokens require at least one `--operator-id`.
Selector and resource authorization remain enforced by CharterOS.

## Convenience Make targets

```bash
make dev-identity-init
make dev-identity-env
make dev-identity-serve
make dev-identity-token
```

`dev-identity-env` prints shell exports; use `eval "$(make -s dev-identity-env)"` when you want to apply them
to the current shell.

## Reset

Stop the JWKS service and remove the local state:

```bash
rm -rf .charteros-dev
```

Any previously minted local token will then become unusable after the identity is regenerated because the
signing key changes.
