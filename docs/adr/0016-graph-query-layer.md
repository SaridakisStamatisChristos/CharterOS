# ADR 0016 — Bounded Charter Graph Query Layer

## Status

Accepted for Roadmap PR16.

## Context

PR15 established the Charter Graph as a versioned, event-derived PostgreSQL projection with an
explicit active version, deterministic rebuild, verification, per-aggregate causal cursors, and
PR14 consumer deduplication. The roadmap now requires useful graph queries without exposing an
arbitrary graph language or allowing the projection to become transactional authority.

The current event contract intentionally does not duplicate every canonical field into graph
nodes. For example, airport coordinates remain canonical catalog data, while aircraft positions
preserve event-time and knowledge-time facts in the graph. PR16 must therefore use graph topology
and history where the projection is authoritative for derived relationships, while consulting
canonical reference/read models for facts that were never projected.

## Decision

### Typed query surface only

PR16 exposes bounded HTTP operations under:

```text
/v1/graph
```

The supported operations are:

- aircraft near an airport at event time `T`, with an explicit knowledge cutoff;
- aircraft feasible for a mission;
- operator route history;
- quote history and revision lineage;
- historical aircraft-position reconstruction;
- structural empty-leg candidates between planned bookings;
- booking-to-flight lineage.

No Cypher, Gremlin, SQL, arbitrary traversal expression, or client-supplied graph program is
accepted.

### Active projection contract

Every PR16 graph query requires exactly one PR15 projection with status `active`.

If there is no active projection, or the projection metadata is inconsistent, the query fails
closed. PR16 does not silently fall back to a mutable ad-hoc graph assembled from canonical tables.

### Authority boundary

The Charter Graph remains a derived read model.

PR16 uses:

- graph nodes/edges for projected identity, topology, lineage, revision relationships, and
  bitemporal position history;
- immutable outbox event envelopes for quote event history;
- canonical airport coordinates for geometry because PR15's airport event does not contain
  latitude/longitude;
- the existing PR6 matching policy and canonical matching snapshots for mission feasibility.

This is deliberate. PR16 does not invent graph attributes that were never emitted by authoritative
events.

### Bitemporal position semantics

Historical position reconstruction applies both cutoffs:

```text
position.event_time <= requested event time
position.knowledge_time <= known_as_of
```

Among visible observations, the deterministic winner is the maximum by:

```text
event_time, knowledge_time, position_id
```

The same rule is used by the near-airport query. A late observation may therefore change a later
reconstruction without leaking into an earlier historical decision.

### Aircraft near airport

The near-airport query reconstructs one visible position per aircraft, resolves airport-backed
position observations through canonical airport coordinates when necessary, computes deterministic
great-circle distance using the existing CharterOS distance primitive, then applies the requested
radius and stable ordering.

The v1 query is bounded and fails closed if the projected position set exceeds its explicit scan
guard. PostGIS or an additional spatially indexed projection may replace this implementation later
without changing the API contract.

### Mission feasibility

PR16 does not create a second feasibility algorithm.

`/missions/{id}/feasible-aircraft` requires an active graph projection and delegates candidate
evaluation to the existing matching policy, including:

- aircraft/operator operational constraints;
- bitemporal position evidence;
- availability evidence;
- range;
- repositioning;
- reference performance/cost profile;
- deterministic reason codes and ranking.

This prevents semantic drift between the established matching API and the graph query layer.

### Operator route history

Route history is reconstructed from active graph lineage:

```text
operator <- WITH_OPERATOR - booking
mission - HAS_BOOKING -> booking
mission - ORIGIN -> airport
mission - DESTINATION -> airport
booking - USES_AIRCRAFT -> aircraft
booking - ACCEPTED_QUOTE -> quote
```

Airport identifiers and ICAO codes are returned with the projected mission departure window.

### Quote history

Quote history combines two forms of evidence:

1. active graph `HAS_QUOTE` topology for revision lineage within the RFQ;
2. immutable quote outbox envelopes ordered by aggregate version for the requested quote's event
   history.

The query does not treat the mutable current quote row as historical truth.

### Empty-leg candidates

PR16 identifies **structural repositioning gaps**, not optimized empty legs.

For consecutive planned bookings of the same aircraft, a candidate exists when:

- the previous mission destination differs from the next mission origin;
- the previous mission departure window ends no later than the next mission departure window
  begins;
- the gap intersects the caller's bounded query window.

The returned interval is evidence of a possible repositioning requirement. It is not a claim that
the repositioning flight is operationally feasible, profitable, or optimal.

Those calculations belong to PR18.

### Booking-to-flight lineage

The current CharterOS domain has no canonical `Flight` aggregate.

PR16 therefore returns the strongest supported lineage:

```text
booking
 -> mission
 -> planned origin/destination and departure window
 -> accepted quote
 -> operator
 -> aircraft
```

The response explicitly sets:

```text
flight_entity_id = null
lineage_status = planned_route_only
```

PR16 does not fabricate a Flight identifier or silently equate Mission with a canonical Flight
entity. A later flight/operations aggregate can extend this contract.

### Transaction and consistency model

API handlers run graph reads in PostgreSQL `REPEATABLE READ, READ ONLY` transactions so a single
response observes a stable database snapshot.

Query results are bounded with explicit result limits and internal scan guards. Projection
inconsistency, ambiguous lineage, missing required references, malformed projected attributes, or
multiple active versions fail closed.

### API surface

PR16 adds:

```text
GET /v1/graph/aircraft/near-airport
GET /v1/graph/aircraft/{aircraft_id}/historical-position
GET /v1/graph/missions/{mission_id}/feasible-aircraft
GET /v1/graph/operators/{operator_id}/route-history
GET /v1/graph/quotes/{quote_id}/history
GET /v1/graph/empty-leg-candidates
GET /v1/graph/bookings/{booking_id}/flight-lineage
```

### Testing

PR16 tests cover:

- historical position reconstruction with late-known facts;
- near-airport no-hindsight behavior;
- operator route lineage;
- quote revision and immutable event history;
- structural empty-leg detection;
- booking planned-flight lineage without a fabricated Flight entity;
- mission feasibility through the existing matching policy on an active rebuilt projection.

## Explicit non-goals

PR16 does not implement:

- arbitrary Cypher/Gremlin/query-language exposure;
- graph-backed command handling;
- canonical business writes through graph tables;
- a Flight aggregate;
- tender/reverse-auction semantics from PR17;
- empty-leg optimization, min-cost-flow, profitability, or opportunity-cost logic from PR18;
- market simulation from PR19;
- ML ranking;
- FX conversion.

## Consequences

PR16 turns the PR15 projection into a useful, bounded read layer while preserving the existing
authority model and deterministic historical semantics. It deliberately keeps optimization and new
transactional concepts out of scope so PR17 and PR18 can build on a stable query contract.
