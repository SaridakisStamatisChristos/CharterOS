# ADR 0014 — Production Transactional Outbox Delivery

## Status

Accepted for Roadmap PR14.

## Context

CharterOS has emitted immutable domain events into `outbox_events` since the catalog foundation. Those rows are committed in the same PostgreSQL transaction as authoritative domain mutations, but before PR14 they had no production delivery authority: no lease ownership, retry schedule, poison state, worker, fencing, or durable consumer deduplication.

PR14 must turn the existing write-side outbox into the delivery backbone for later projections without pulling PR15 Charter Graph work forward or introducing Kafka, Redis, Celery, or microservices.

## Decision

### Delivery guarantee

CharterOS provides **at-least-once** event delivery.

It does not claim impossible generic exactly-once delivery across arbitrary external side effects. Every event has a stable `event_id`; delivery may be retried after ambiguous worker failure. Consumers must therefore be idempotent.

### Canonical delivery state

Each `outbox_events` row carries durable delivery metadata:

- `delivery_status`: `pending | in_flight | retry | delivered | poisoned`;
- `available_at`: earliest retry/claim time;
- lifetime `publish_attempts`;
- current retry-cycle `delivery_attempts`;
- `last_attempt_at`;
- bounded `last_error`;
- `lease_owner`;
- UUID `lease_token`;
- `lease_expires_at`;
- existing `published_at` as successful delivery time;
- `poisoned_at`.

The immutable canonical event envelope is never rewritten.

### Claiming and fencing

Workers claim due rows using PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`.

A claim:

1. changes the row to `in_flight`;
2. increments lifetime and current-cycle attempts;
3. writes worker identity;
4. creates a fresh UUID lease token;
5. records lease expiry.

Every completion/failure update must match both `event_id` and the active lease token. A stale worker therefore cannot commit delivery metadata after another worker has reclaimed the event.

Expired in-flight work is reclaimable while retry budget remains. An expired lease that already consumed the final configured attempt is quarantined as poison instead of replayed indefinitely.

### Retry and poison policy

Retry delay is deterministic capped exponential backoff:

```text
delay = min(base_seconds * 2^(attempt - 1), max_seconds)
```

No random jitter is used in the canonical algorithm so tests and incident reconstruction remain deterministic. Deployment-level worker staggering may still be used.

After the configured number of attempts in the current cycle, another delivery failure moves the event to `poisoned`. Poisoned events are excluded from automatic claims.

An operator may explicitly requeue a poison event. Manual requeue resets only the current-cycle attempt counter; lifetime `publish_attempts` remains monotonic so forensic history is preserved.

### Consumer idempotency

`outbox_consumer_receipts` stores one durable receipt per:

```text
(consumer_name, event_id)
```

The SQLAlchemy idempotent-consumer runner takes a PostgreSQL transaction-scoped advisory lock for that pair, checks the receipt, executes the consumer handler, and writes the receipt in the same transaction.

For database-backed projections this makes projection mutation and deduplication atomic. Concurrent duplicate delivery invokes the handler exactly once.

External side-effecting consumers must use the stable `event_id` as their own idempotency identity or provide an equivalent durable deduplication contract.

### Worker composition

`apps.outbox_worker.main` is a dedicated process with:

- bounded claim batches;
- poll interval;
- lease duration;
- maximum attempt policy;
- deterministic retry backoff;
- graceful SIGTERM/SIGINT shutdown;
- one-shot mode for operations/tests;
- explicit poison requeue command.

The reference publisher emits the canonical envelope to structured JSON logging. The delivery engine depends only on the `OutboxPublisher` port, so a broker adapter or PR15 in-process projection dispatcher can replace it without changing lease/retry semantics.

### Transactional boundary

Domain-event insertion remains in the same database transaction as the aggregate mutation. PR14 does not change that contract.

Delivery happens asynchronously after commit. A transaction rollback therefore leaves neither the domain mutation nor its outbox event visible to the worker.

### Ordering

Claims are ordered by `recorded_at, event_id` among currently due rows. PR14 does not claim global total ordering across independent worker processes.

Per-aggregate causality remains reconstructable from:

```text
aggregate_type + aggregate_id + aggregate_version
```

with the existing unique constraint. Consumers that require strict per-aggregate processing must enforce that requirement using aggregate version/checkpoint semantics.

### Migration

Alembic `0012_transactional_outbox` extends existing rows without deleting historical events. Existing unpublished rows become `pending`; any historically published rows become `delivered`.

Downgrade is refused once delivery or consumer-receipt evidence exists.

## Consequences

PR14 provides a race-safe, retryable, auditable outbox worker and an idempotent-consumer primitive while keeping PostgreSQL authoritative and leaving PR15 graph projection, payment processing, disruption handling, reconciliation, and FX out of scope.
