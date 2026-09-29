# Authentication and authorization boundary

CharterOS protects every `/v1` route with a centralized, deny-by-default authentication and
capability boundary. `/health` is public. In non-production environments only, `/docs` and
`/openapi.json` are also public; production disables both documentation and the OpenAPI surface.

## Identity verification

Production authentication accepts bearer JWTs only after asymmetric cryptographic verification
against the configured OIDC-compatible JWKS endpoint. Verification requires an allowed algorithm,
`kid`, signature, issuer, audience, `exp`, `iat`, `nbf` when present, `sub`, and CharterOS
application claims. `alg=none` and symmetric HMAC algorithms are rejected. JWKS retrieval uses
HTTPS, a bounded key-count/TTL cache, synchronous refresh for unknown or rotated keys, and fails
closed when no valid cached key can satisfy verification.

Required CharterOS claims are:

- `charteros_principal_type`: `buyer`, `operator`, `administrator`, or `service`.
- `charteros_permissions`: an array of exact capability strings defined by `Permission`.
- `charteros_buyer_ids`: UUID array of buyer organizations represented by a buyer principal.
- `charteros_operator_ids`: UUID array of operators represented by an operator principal.

The `X-Buyer-Id` and `X-Operator-Id` headers are selectors only. They never authenticate a caller,
and they cannot select an organization/operator outside the verified token memberships. An
administrator may cross tenant boundaries only with the explicit `tenant:admin` capability.

## Route policy

`apps/api/route_security.py::ROUTE_POLICIES` is the authoritative exhaustive route inventory. CI tests
compare it to every FastAPI `/v1` route, so adding a route without an explicit policy fails the
test suite. The policy records the required capability, allowed principal type, selector
requirement, and—where needed—resource ownership requirement.

The major route classes are:

| Surface | Principal / capability model |
| --- | --- |
| Buyer portal | buyer membership + buyer capabilities + `X-Buyer-Id` selector |
| Operator portal | operator membership + operator capabilities + `X-Operator-Id` selector |
| Tender buyer lifecycle | buyer selector + mission/tender ownership checked in PostgreSQL |
| Tender supplier lifecycle | operator selector + invitation ownership checked in PostgreSQL |
| Sealed tender supplier view | operator selector + invitation/tender/operator binding |
| Disruption / reconciliation | buyer/operator selector plus existing domain party checks |
| Evidence | authenticated buyer/operator/admin with `audit-evidence:read` |
| FX/catalog/global graph/legacy unscoped APIs | administrator-only explicit capabilities |
| Worker processes | non-HTTP service permissions; no human-admin implication |

Legacy APIs that lack a trustworthy tenant selector are deliberately administrator-only. Buyer and
operator traffic must use the scoped portal surfaces instead of relying on caller-controlled body or
path IDs as implicit authority.

## Failure semantics

- Missing or invalid authentication: `401 Unauthorized` and `WWW-Authenticate: Bearer`.
- Valid principal without the required principal type, permission, selector membership, or resource
  ownership: `403 Forbidden`.
- Resource-scope authorization failures collapse to `403` to avoid leaking cross-tenant existence.
- Existing domain validation/conflict/not-found behavior is preserved after authorization succeeds.

Bearer tokens are never included in security logs. Denial logs contain only verified subject/type
when available, route metadata, and a coarse failure reason.

## Test seam

Tests inject an `AuthenticationBackend` through `create_app`. The production default is a rejecting
backend until OIDC settings are configured; there is no environment-variable or header-based bypass.
The pytest fixture's trusted administrator is test-process-only and is injected by monkeypatching the
backend factory, so legacy domain tests can exercise their original behavior without shipping a
production authentication escape hatch.
