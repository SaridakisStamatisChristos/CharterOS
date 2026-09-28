# ADR 0004 — Bitemporal aircraft position and availability timeline

## Status

Accepted for Roadmap PR4.

## Context

CharterOS matching and procurement decisions will eventually depend on where an aircraft was and
whether it was available at a particular operational time. A normal "latest row wins" model leaks
hindsight into backtests and audit reconstruction when a real-world fact arrives late. PR4 therefore
needs both **valid/event time** (when a fact applies in the world) and **knowledge/recorded time**
(when CharterOS learned the fact).

PostgreSQL remains the canonical transactional store. The standalone Charter Graph/NetworkX
package is a deterministic reference and simulation asset only.

## Decision

### Position observations

Positions are append-oriented facts. Each observation stores an airport **or** an explicit coordinate
pair, event time, recorded time, source, and provenance. Conflicting observations are not silently
overwritten. For state at `(event_time=T, known_as_of=K)`, CharterOS considers only rows with
`event_time <= T` and `recorded_at <= K`, then chooses the greatest
`(event_time, recorded_at, id)`. The UUID is only the final deterministic tie-breaker.

A position observation cannot be recorded before its observation/event time. All datetimes are
canonical timezone-aware UTC values.

### Availability

Availability is represented by half-open intervals `[valid_from, valid_to)` with a constrained
status: `available`, `reserved`, `maintenance`, or `unknown`. `valid_to <= valid_from`
is invalid.

Currently authoritative availability intervals for one aircraft are mutually exclusive. A new
non-correction write that overlaps an authoritative interval returns an explicit conflict.

Corrections are explicit: a replacement may specify `supersedes_id`. The prior row remains stored,
its knowledge validity is closed at the replacement's `recorded_at`, and the replacement is
inserted in the same transaction. Thus contradictory historical facts remain auditable:

- for `K < superseded_at`, the prior fact remains authoritative;
- for `K >= superseded_at`, the replacement is authoritative;
- at the exact correction boundary the old fact is excluded and the new fact is included.

Only one direct replacement may supersede a given availability row.

### Concurrency and database enforcement

Every fleet mutation runs inside the existing request transaction and first acquires the existing
idempotency advisory lock for its endpoint/key. The fleet service then reads the target aircraft with
`SELECT ... FOR UPDATE`, serializing timeline writes for that aircraft and eliminating the overlap
check/insert TOCTOU race.

PostgreSQL adds a second line of defense: a partial GiST exclusion constraint, enabled by
`btree_gist`, prevents overlapping `[valid_from, valid_to)` ranges among rows whose
`superseded_at IS NULL`. A unique constraint also prevents a record from being superseded twice.
Foreign keys retain `ON DELETE RESTRICT`.

### Historical reconstruction and bounded reads

`GET /v1/aircraft/{id}/timeline` requires `from` and `to`, accepts `known_as_of`, optional
`at`, and a bounded per-stream `limit` (maximum 500). The returned facts expose event/valid time,
knowledge time, source, and provenance. `at` additionally reconstructs the authoritative fleet state
at that event time under the supplied knowledge cutoff.

No fact whose `recorded_at` is after `known_as_of` can affect either the returned visible history
or the reconstructed state. This is the hard **no-hindsight** invariant.

### Events and replay

`AIRCRAFT_POSITION_RECORDED` and `AIRCRAFT_AVAILABILITY_CHANGED` are persisted through the
existing transactional outbox. Their payloads include fact IDs, temporal dimensions, source,
provenance, and correction lineage so later Charter Graph projections can be rebuilt from canonical
state/events.

### NetworkX boundary

NetworkX is allowed for deterministic test/reference models, bounded graph projections, simulation,
matching, min-cost-flow, repositioning optimization, and algorithm experiments. It is prohibited as
the canonical persistent datastore or shared mutable fleet truth. PR4 introduces no production graph
database or graph projection.

## Consequences

The model is intentionally stricter than a mutable schedule table: corrections are visible in audit
history and historical decisions are reproducible. Timeline writes serialize per aircraft, which is a
reasonable correctness/performance tradeoff at the modular-monolith stage. Later projection and
matching work can consume the outbox without changing PR4's canonical semantics.
