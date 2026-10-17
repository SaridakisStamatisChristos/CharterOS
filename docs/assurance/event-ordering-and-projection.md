# Event ordering and graph-projection assurance

This document records the durable correctness contract for event delivery and Charter Graph projection.

## Authority model

PostgreSQL business state is authoritative. Domain events are appended transactionally with the state they describe. The Charter Graph is a rebuildable, versioned read model and never becomes command authority.

Causal ordering is defined per aggregate by:

```text
aggregate_type + aggregate_id + aggregate_version
```

CharterOS does not claim a global event-time ordering across independent aggregates.

## Delivery contract

The outbox provides at-least-once delivery with stable event IDs, fenced leases, deterministic retry, poison quarantine/requeue and durable consumer receipts. Projection consumers must be idempotent and must not advance across aggregate-version gaps.

## Projection contract

A graph projection records versioned nodes/edges plus aggregate cursors and consumer receipts. Verification independently reconstructs expected projection state from canonical event history and checks event coverage, cursor continuity, node/edge state, endpoint integrity and deterministic digest.

Rebuild lifecycle:

```text
building -> verified -> active -> retired
```

A rebuild does not overwrite the active projection and activation requires an explicit controlled boundary.

## Failure cases covered by verification

The regression suite covers reversed delivery, gaps, duplicate delivery, worker races, worker failure before or after projection commit, poison/requeue convergence, delayed duplicates and full projection rebuild convergence.

These checks demonstrate repository-level behavior under the tested PostgreSQL/runtime configuration; they do not claim generic exactly-once external side effects.
