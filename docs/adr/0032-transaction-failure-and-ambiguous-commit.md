# ADR 0032 — Transaction Failure and Ambiguous-Commit Semantics

## Status

Accepted for PR43.

## Context

CharterOS already commits authoritative aggregate mutations, aircraft-capacity reservations,
decision evidence, idempotency records, and outbox events in PostgreSQL transactions. PR38–PR42
proved the business invariants under logical concurrency, but a production system must also define
what happens when the database, connection, pool, API process, or outbox worker fails at an
unfortunate transaction boundary.

The dangerous case is not only a known rollback. A server can commit and then lose the connection
before the client observes the commit response. Retrying such a command as if it rolled back can
create duplicate effects unless the retry is reconciled against canonical state.

PR43 therefore hardens the transaction machinery without creating a second source of truth, a
second queue, or a generic retry decorator.

## Decision

### Failure taxonomy

Database-facing code uses the following explicit taxonomy:

| Category | Meaning | Retry policy |
| --- | --- | --- |
| `safe_transient_failure` | The current attempt is known to have failed before commit and PostgreSQL reports a bounded transient condition | May retry at a proven replay-safe outer transaction boundary |
| business conflict | Canonical business state rejects the command | Never blindly retry |
| `integrity_violation` | A database integrity rule rejects the attempted state | Fail closed |
| `ambiguous_commit_outcome` | Connection loss occurs while observing commit; the server may already have committed | Never replay blindly; reconcile using the same idempotency key/canonical state |
| `permanent_infrastructure_failure` | Pool exhaustion or another unclassified/non-transient database failure | Surface bounded service failure |

The classifier intentionally does **not** treat arbitrary `SQLAlchemyError` as retryable.

PostgreSQL conditions explicitly treated as rollback-safe during transaction execution are:

- `40001` — serialization failure;
- `40P01` — deadlock victim;
- `55P03` — lock not available / lock timeout;
- `57014` — query cancellation / statement timeout;
- connection-class failures before commit, including invalidated connections and selected
  shutdown/recovery states.

Integrity SQLSTATE class `23` fails closed.

Pool checkout timeout is not immediately retried in-process because doing so under saturation would
amplify load.

### Retry boundary

The PR43 transaction runner is an **outer transaction boundary**, not an inner repository retry.

Its default policy is two total attempts: the original attempt plus at most one retry. The bound is
deliberately small and code-enforced.

Before commit, SQLAlchemy's unit of work is explicitly flushed so deferred database errors are
classified as execution failures whenever possible. On a known rollback-safe transient failure the
failed Session is rolled back or invalidated, then the *entire* transaction action is replayed.

No retry occurs inside a partially executed domain transaction.

### Which commands use in-process retry

PR43 applies the new retry/reconciliation boundary to the three award authorities that already
converge on `BookingService.accept_quote()`:

1. direct Quote award;
2. Buyer Procurement Approval award;
3. Tender award.

These are the highest-risk commands because they atomically create a Booking, reserve aircraft
capacity, mutate Quote/Mission state, append award decision evidence, append outbox events, and
persist the command response.

Each route already requires `Idempotency-Key`, takes the PostgreSQL transaction-scoped idempotency
advisory lock, and stores the request hash and successful response inside the same authoritative
transaction. This makes replay of the *whole transaction* safe.

The PR43 route audit confirmed that mutation modules already expose bounded `Idempotency-Key`
headers. PR43 does not mechanically wrap every mutation in automatic in-process retries. Existing
commands remain safely client-replayable through their transactional idempotency records; an
unclassified database failure is surfaced as a bounded service failure instead of being guessed
retryable.

### Ambiguous commit

A connection failure while executing `COMMIT` is treated differently from a failure known to occur
before commit.

The runner:

1. does **not** assume rollback;
2. does **not** immediately execute the domain command again;
3. resets/invalidate the failed client Session;
4. starts a fresh transaction;
5. uses the exact same idempotency scope/key/request hash;
6. reads the committed canonical response;
7. returns that response when it exists;
8. otherwise returns an explicit ambiguous-commit service failure rather than guessing.

A later client retry with the same key follows the same canonical idempotency path.

### Error contract

Database infrastructure failures do not leak SQL text, DSNs, table internals, stack traces, or
credentials.

Stable API behavior is:

- business/domain conflict: existing `409` contract;
- database integrity failure reaching the transaction boundary: `409 integrity_failure`;
- known or unclassified infrastructure failure: `503 temporarily_unavailable`;
- unreconciled ambiguous commit: `503 ambiguous_commit_retry_same_idempotency_key`.

The SQLSTATE may be recorded as bounded operational metadata; raw SQL and database parameters are
not exposed.

### Connection pool bounds

Production engine construction now has explicit validated settings:

- pool size: default 10;
- max overflow: default 10;
- checkout timeout: default 5 seconds;
- connection recycle: default 1800 seconds;
- connect timeout: default 5 seconds;
- `pool_pre_ping=True`.

The limits are deliberately finite. Pool exhaustion must become a bounded failure, not an unbounded
request hang.

### Timeout policy

PR43 does not add one global timeout capable of interrupting arbitrary transactions at unsafe
points.

Instead:

- connection establishment has a bounded connect timeout;
- pool checkout has a bounded checkout timeout;
- existing JWKS HTTP fetch retains its independent HTTP timeout;
- PostgreSQL lock and statement cancellation semantics are exercised with transaction-local
  settings in chaos tests;
- expensive matching/optimizer request budgets remain PR44 scope.

This preserves explicit ownership of cancellation semantics.

### Outbox failure semantics

PR43 preserves ADR 0014's at-least-once outbox design.

Chaos coverage now proves:

- a worker can die while a lease is held;
- an expired lease is safely reclaimable;
- a stale lease token cannot acknowledge reclaimed work;
- a consumer side effect and durable receipt can commit before the delivery ACK is lost;
- redelivery observes the durable receipt and does not repeat that side effect;
- an expired final attempt becomes poisoned deterministically;
- poison does not loop automatically;
- explicit poison requeue semantics remain unchanged.

No second queue or generic exactly-once claim is introduced.

### Fault injection

Faults are injected only through tests:

- real PostgreSQL advisory-lock deadlock;
- transaction-local lock timeout;
- transaction-local statement timeout;
- PostgreSQL backend termination;
- bounded QueuePool exhaustion;
- test-only monkeypatches after award state has been staged;
- synthetic loss of a successful commit response.

No production chaos environment flag or caller-reachable failure switch is added.

## Invariants preserved

PR43 does not change:

- PostgreSQL as canonical truth;
- `BookingService.accept_quote()` as the award authority;
- PR38 PostgreSQL exclusion constraint as the final same-tail overlap arbiter;
- PR39 `reserved -> released` capacity lifecycle;
- PR40/PR42 canonical aircraft feasibility;
- immutable Quote revision and approval lineage;
- no-hindsight evidence semantics;
- evidence integrity rules;
- deny-by-default authorization;
- transactional outbox event identity or delivery algorithm.

A retry can delay or reject a command, but it may not bypass any of those invariants.

## Verification

PR43 adds deterministic/unit and PostgreSQL integration coverage for:

- SQLSTATE taxonomy;
- bounded retry count;
- real deadlock victim retry;
- lock timeout;
- statement cancellation;
- connection loss before commit;
- ambiguous commit after successful server commit;
- award failure after authoritative effects are staged but before commit;
- lost HTTP response followed by same-key award replay;
- simultaneous duplicate same-key award;
- cross-Mission capacity contention combined with a transient database failure;
- pool exhaustion;
- outbox lease crash/reclaim;
- consumer deduplication after ACK loss;
- stale lease fencing;
- final-attempt poison.

CI also performs a PostgreSQL service restart probe so a checked-out connection is observed failing
after restart and a fresh pre-ping-enabled connection recovers.

## Non-goals

PR43 does not add rate limiting, GDPR/data-lifecycle workflows, a new deployment platform,
Kubernetes, backup provisioning, release signing, payment semantics, or matching/business-policy
changes. Those remain later roadmap concerns.
