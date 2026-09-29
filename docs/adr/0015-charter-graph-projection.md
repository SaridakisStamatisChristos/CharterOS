# ADR 0015 — Charter Graph Production Projection v1

## Status

Accepted for Roadmap PR15.

## Context

CharterOS needs a graph-shaped read model for later route, fleet, quote, booking, and lineage
queries. The authoritative business state already lives in PostgreSQL domain tables and immutable
`outbox_events`. PR14 added at-least-once delivery, fenced leases, retry/poison handling, and
durable consumer receipts. PR15 must build on those guarantees without making graph state
transactional truth or pulling PR16 graph query APIs forward.

## Decision

### Purpose and authority

The Charter Graph is a **derived projection only**. PostgreSQL domain state and the immutable
event envelopes remain authoritative. Graph rows may be discarded and rebuilt from event history.
No command path writes business truth through the graph.

### Storage choice

PR15 stores the production projection in PostgreSQL tables rather than introducing a second
operational database. This keeps projection mutation and PR14 consumer receipts in one local ACID
transaction and keeps deployment complexity bounded. NetworkX remains suitable for bounded
reference/testbench computation but is not canonical storage.

### Projection identity and versioning

The projection identity is:

```text
projection_name = charter_graph
projection_version = positive integer
```

Every node, edge, cursor, and checkpoint is namespaced by both values. A graph interpretation
change requires a new projection version. Versions move through:

```text
building -> verified -> active -> retired
```

Only one version can be active. Rebuilds never overwrite an active version.

### Graph schema

PR15 projects these node types where emitted events support them:

- `operator`
- `aircraft`
- `airport`
- `mission`
- `rfq`
- `quote`
- `booking`
- `aircraft_position`

Representative relationships include:

- operator `OPERATES` aircraft;
- aircraft `HOME_BASE` airport;
- aircraft `HAS_POSITION` aircraft position;
- aircraft position `AT_AIRPORT` airport when the event records an airport;
- mission `ORIGIN` / `DESTINATION` airport;
- mission `HAS_RFQ` RFQ;
- RFQ `SENT_TO` operator and `RECEIVED_QUOTE` quote;
- RFQ `HAS_QUOTE` quote;
- quote `PROPOSES_AIRCRAFT` aircraft;
- quote revision/supersession and award lineage edges;
- mission `HAS_BOOKING` booking;
- booking `ACCEPTED_QUOTE`, `WITH_OPERATOR`, and `USES_AIRCRAFT`.

Position observations are retained as separate nodes with event time and knowledge time. The
projection therefore preserves the bitemporal no-hindsight facts required by the Charter Graph
testbench instead of collapsing positions into a mutable "latest position" field.

### Event mapping contract

Projection mapping consumes only immutable canonical event envelopes. It validates that the
canonical JSON agrees with the outbox envelope identity before interpreting the payload.

For projected aggregate types, an unknown event type fails closed. It is not silently ignored,
because doing so would advance the aggregate cursor while losing graph meaning. Aggregate types
outside the PR15 graph contract are ignored by this consumer and remain handled by the normal
outbox pipeline.

### Ordering semantics

PR14 does not provide a global total order, and PR15 does not invent one from `recorded_at`.
Ordering authority is per aggregate:

```text
aggregate_type + aggregate_id + aggregate_version
```

Each projection version stores a durable per-aggregate cursor. The next accepted event must be
exactly `last_aggregate_version + 1` (or version 1 for a new aggregate). A future version produces
a gap error and remains retryable. A stale version without the matching durable receipt is treated
as a consistency failure.

Cross-aggregate edges may temporarily point at nodes whose event is delivered later. Verification
requires every persisted edge endpoint to exist before a projection version is considered valid.

### Idempotency and atomicity

The graph consumer reuses PR14 `outbox_consumer_receipts`. Its consumer identity includes the
projection version:

```text
charter_graph:v<projection_version>
```

For one event, graph mutation, aggregate cursor movement, durable checkpoint movement, and the
consumer receipt execute in the same database transaction. A failure rolls all of them back.
Concurrent duplicate deliveries serialize through PR14's transaction-scoped advisory lock and
produce one mutation/receipt.

If the graph transaction commits but a later outbox publishing step or delivery acknowledgement
fails, PR14 may redeliver the same event. The existing receipt makes that replay harmless.

### Durable checkpoint

Every projection version has a durable checkpoint containing:

- processed projected-event count;
- greatest observed `recorded_at` horizon;
- deterministic event-id tie-break value at that horizon;
- checkpoint update time.

The checkpoint is a durable summary, not a fictional global sequence. Exact restart and causality
are jointly established by consumer receipts plus per-aggregate cursors. Verification checks all
three against authoritative history.

### Rebuild semantics

`python -m apps.graph_projection.main rebuild --target-version N` creates or resumes an isolated
`building` target and replays all authoritative projected event envelopes in deterministic
per-aggregate order. `--reset-building` is explicit and may reset only a `building` version; it
never destroys an active or verified projection.

A successful rebuild runs verification and records the deterministic graph digest before marking
the target `verified`.

Activation is an explicit, controlled pointer switch. It requires `--maintenance-mode`; callers
must quiesce writes and outbox workers for the final rebuild/verification/switch window. The old
active projection is retained as `retired`. PR15 does not silently delete a usable version.

### Verification semantics

`python -m apps.graph_projection.main verify --version N` independently rebuilds an in-memory
reference graph from immutable outbox history and compares it with persisted projection state. It
checks:

- projected event count versus PR14 consumer receipts;
- projected event count versus durable checkpoint;
- per-aggregate cursor continuity and final versions;
- missing or unexpected nodes;
- missing or unexpected edges;
- invalid edge endpoints;
- deterministic reference digest versus persisted digest;
- drift from the last verified digest.

A failed verification exits non-zero in the CLI.

### Failure and rollback behavior

Projection errors do not mutate canonical domain state. During live delivery the graph publisher
runs before the reference logging publisher. If graph processing fails, the publisher raises and
PR14 keeps the event retryable according to its existing retry/poison policy. No consumer receipt
commits when graph mutation fails.

Poison handling remains PR14's responsibility; PR15 does not weaken lease fencing, retry budget,
or manual requeue semantics.

### Testbench relationship

`test-assets/charter-graph/` remains deterministic reference material. PR15 cross-validates the
`charter_graph_testbench_v0.1.0` no-hindsight position dataset by projecting both observations and
preserving `event_time` and `knowledge_time`. NetworkX is not required for the production storage
path and is not promoted to canonical authority.

## Operational commands

```text
python -m apps.graph_projection.main status
python -m apps.graph_projection.main rebuild --target-version 1
python -m apps.graph_projection.main verify --version 1
python -m apps.graph_projection.main activate --version 1 --maintenance-mode
python -m apps.graph_projection.main rebuild --target-version 1 --activate --maintenance-mode
```

Activation is available either as a standalone command for an already verified version or as the
final step of `rebuild --activate`. Both paths require `--maintenance-mode`. Operators should stop
writers and outbox workers before the pointer switch and resume them only after activation succeeds.

## Explicit non-goals

PR15 does not implement:

- PR16 graph query APIs;
- arbitrary Cypher exposure;
- graph-backed transactional commands;
- a new graph database;
- ML ranking or optimization;
- payments, disruption, reconciliation, or FX policy;
- hidden cross-currency normalization.

## Consequences and path to PR16

PR15 provides a deterministic, durable, versioned, auditable graph read model that can be rebuilt
from event history and safely retried under PR14. PR16 may now build bounded query services over
the active projection while continuing to treat canonical PostgreSQL domain state and events as
the source of truth.
