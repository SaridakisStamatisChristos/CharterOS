# ADR 0033 — API Abuse and Resource-Exhaustion Boundaries

## Status

Accepted for PR44.

## Context

CharterOS already authenticates and authorizes every protected route and PR43 bounds database pool
failure. Authorization alone does not constrain how much work an allowed caller can force the
service to perform.

PR44 addresses valid-looking abusive traffic without introducing a process-local limiter that would
become incorrect as soon as the API has more than one replica.

## Decision

### General request-rate authority

General request-rate enforcement and anonymous/authentication flood control are delegated to a
**trusted ingress/API-gateway layer**.

Staging and production fail startup unless
`CHARTEROS_TRUSTED_INGRESS_RATE_LIMIT_ENFORCED=true` is explicitly configured. This flag is a
deployment contract, not a claim that the application can verify the gateway configuration.

The ingress policy must key authenticated traffic by the verified identity/tenant context available
at the gateway and must apply independent anonymous/source controls before requests consume API
workers. CharterOS does not ship a misleading per-process general limiter.

### Application-owned expensive-work budgets

CharterOS independently protects the work it owns even behind the gateway.

The following workload classes use a PostgreSQL-backed fixed-window budget:

- matching;
- reposition optimization / optimized empty-leg visibility;
- audit evidence reconstruction;
- Charter Graph queries.

The rate-window row is keyed by:

`budget + SHA-256(authenticated issuer, subject, principal type, validated tenant selector) + window`

Raw principal and tenant identifiers are not persisted in the limiter table.

The PostgreSQL `INSERT ... ON CONFLICT ... WHERE request_count < limit` operation is the shared
serialization point, so separate API replicas observe one budget. Exhaustion returns HTTP `429`
with `detail=resource_limit_exceeded` and a bounded `Retry-After` value.

The default window is 60 seconds. Limits are configuration, not hard-coded business policy.

### Request-body boundary

A pure ASGI middleware buffers request bodies before authentication, Pydantic parsing, repository
work, or domain transactions.

Default bounds:

- maximum body: 1 MiB;
- maximum JSON nesting depth: 32;
- total request-body receive time: 10 seconds.

A declared `Content-Length` above the bound fails before body read. Chunked/undeclared bodies are
counted while being read. JSON depth is scanned without trusting the JSON parser to recurse without
limit.

Stable failures are:

- `413 request_too_large`;
- `422 request_complexity_exceeded`;
- `408 request_body_timeout`;
- `400 invalid_content_length`.

The timeout is deliberately at the **pre-route body-receive boundary**. PR44 does not add a global
request timeout that could interrupt an authoritative transaction at an unsafe point.

### Schema and query bounds

Command collection counts remain explicit Pydantic bounds and relevant free-form list items now
also have per-item text ceilings. Contract metadata has bounded key/value sizes and bounded
cardinality.

No command input is silently truncated.

Historical fleet/operator timeline and calendar reads are bounded to 366 days. Reposition and
optimized empty-leg windows are bounded to 31 days. Existing result/page/event limits remain in
force, including:

- matching/result limits;
- graph result limits;
- evidence event limit;
- operator pagination;
- PR36/PR41 optimizer capacity of 100 structural empty legs and 2,000 quoted opportunities.

### JWKS refresh abuse

The JWKS path keeps the existing TTL, key-count, token-size, asymmetric-algorithm, and HTTP timeout
bounds and adds:

- a maximum JWKS document byte size before JSON parsing;
- lock-protected single-flight refresh;
- a bounded negative cache for unknown `kid` values;
- a minimum interval between unknown-key refreshes;
- a minimum interval between repeated signature-failure forced refreshes.

The first same-`kid` signature failure may still refresh immediately so legitimate key rotation
continues to work. Repeated failures are throttled. New-`kid` visibility may be delayed only by the
configured short negative-cache interval (default 5 seconds).

### Idempotency storage abuse

`Idempotency-Key` remains limited to 128 characters at the API boundary and database column.

Stored replay responses now have a PostgreSQL-enforced maximum serialized JSON size of 256 KiB.
`idempotency_records.created_at` is indexed for retention cleanup.

The default idempotency replay-retention window is explicit and configurable. The cleanup command
deletes only records strictly older than the cutoff, in a bounded `FOR UPDATE SKIP LOCKED` batch.
The boundary record at the cutoff is retained.

Idempotency retention is network/replay protection policy, **not** PR45 commercial
data-governance policy. It does not delete Bookings, Quotes, evidence, outbox history, contracts,
reconciliation data, or other authoritative business records.

Transient API rate-window rows are also cleaned in bounded batches.

Operational command:

```bash
python -m apps.resource_cleanup.main
```

Operators should schedule it repeatedly; one invocation is intentionally bounded.

### Connection/thread exhaustion

PR43 remains the database connection-pool authority: finite pool size, finite overflow, finite
checkout timeout, pre-ping, and recycle policy.

PR44 adds shared expensive-work budgets rather than increasing the pool or serializing the entire
API. Concurrent budget tests use independent SQLAlchemy pools against the same PostgreSQL database
to prove multi-replica atomicity.

### Failure and privacy semantics

Abuse-control logs use workload class and principal type only. They do not log bearer tokens,
database credentials, raw tenant IDs, or raw principal subjects.

Authorization runs before the expensive-work budget is consumed. Tenant selectors are validated
before they contribute to the hashed limiter identity.

## Rejected alternatives

### Process-local general limiter

Rejected because each replica would have an independent counter and effective capacity would rise
with replica count.

### One global application semaphore

Rejected because it would serialize unrelated tenants/workloads and would not coordinate replicas.

### Arbitrary global request timeout

Rejected because cancellation can cross transaction boundaries and create ambiguous behavior.

### Silent truncation

Rejected for command input and optimizer universes. CharterOS fails explicitly when a validated
bound is exceeded.

## Compatibility with prior invariants

PR44 does not change:

- PostgreSQL canonical business authority;
- PR38 aircraft overlap exclusion;
- PR39 capacity release lifecycle;
- PR40/PR42 feasibility;
- PR43 transaction retry/ambiguous-commit policy;
- Quote/approval lineage;
- no-hindsight semantics;
- evidence integrity;
- deny-by-default authorization;
- transactional outbox delivery semantics.

## Verification

PR44 adds tests for:

- oversized request bodies;
- deeply nested JSON;
- slow request bodies;
- command item/cardinality bounds;
- bounded query horizons;
- shared PostgreSQL rate budgets under concurrent independent pools;
- two API replicas sharing one budget;
- tenant-aware budget separation;
- bounded idempotency cleanup;
- database-enforced idempotency response size;
- unknown-`kid` storms;
- repeated invalid-signature storms;
- duplicate/malformed/oversized JWKS documents;
- bounded new-`kid` rotation delay;
- exhaustive expensive-route budget classification.

The existing full CI/security/container/SBOM gate remains mandatory.

## Non-goals

PR44 does not implement:

- tenant deletion, DSAR, anonymization, legal hold, or broad retention governance (PR45);
- backup/restore assurance (PR46);
- release signing/provenance (PR47);
- production SLO/alerting instrumentation (PR48);
- a new ingress product or deployment platform;
- business-policy, matching-policy, pricing, payment, or award changes.
