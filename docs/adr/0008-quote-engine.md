# ADR 0008 — Quote engine

## Status

Accepted for Roadmap PR8.

## Context

PR8 introduces operator-submitted commercial quotes on top of the PR7 RFQ lifecycle. Quotes are
canonical commercial evidence, not normalized buyer-comparison records. PR9 and PR10 own
normalization, confidence/caveat logic, and buyer comparison.

The quote engine must preserve exact money, revision history, deterministic events, idempotent
mutations, and race-safe authoritative-current semantics.

## Decision

### Aggregate and lifecycle

Quote is a framework-independent aggregate. The PR8 implementation exposes only the lifecycle
states needed by this roadmap increment:

- SUBMITTED
- EXPIRED
- WITHDRAWN
- SUPERSEDED

The broader roadmap states DRAFT, UNDER_REVIEW, ACCEPTED, and REJECTED remain reserved for later
workflow. The public submit endpoint creates a quote directly in SUBMITTED.

Expiry and withdrawal are explicit persisted transitions. They are not computed presentation flags.

### RFQ eligibility and the QUOTED transition

Initial quote submission requires the canonical RFQ to be ACKNOWLEDGED. CharterOS does not silently
treat SENT -> QUOTED as an implicit acknowledgement.

Submission must occur strictly before the RFQ response deadline. The first successful submission
atomically advances the RFQ from ACKNOWLEDGED to QUOTED and emits RFQ_QUOTED in the same transaction
as QUOTE_SUBMITTED.

Quote revisions do not re-transition the RFQ and do not advance the mission. PR8 leaves the mission
in SOURCING; buyer-level quote comparison/selection is later roadmap scope.

### Aircraft association

Each quote references one canonical aircraft. The aircraft must exist and must belong to the
operator targeted by the RFQ. PR8 intentionally does not rerun PR6 matching feasibility as hidden
quote validation.

Foreign keys from quotes to RFQs and aircraft use RESTRICT.

### Exact money and structured pricing

A quote has one explicit ISO-style Currency value. Monetary values use signed 64-bit integer minor
units through the existing Money primitive.

The persisted commercial structure is:

- positive base charter price;
- optional non-negative repositioning cost;
- zero or more non-negative structured price-component lines;
- mechanically derived submitted total.

Price component categories are fuel surcharge, airport fees, handling, parking, crew overnight,
catering, deicing, permits, taxes, broker/service fee, and explicitly labelled other.

Component rows do not store an independent currency. They inherit the quote currency, structurally
preventing mixed-currency addition inside a quote. No FX conversion occurs.

The submitted total is not an independent opaque field. It is reconstructed exactly as base price
plus repositioning plus all component amounts. PR8 does not calculate PR9 expected totals,
worst-case totals, confidence, or unresolved-component heuristics.

### Commercial terms

Inclusions and exclusions are whitespace-canonicalized, case-insensitively deduplicated, and may
not overlap. Cancellation and payment terms are persisted as operator-submitted structured text.

### Validity

valid_until is timezone-aware UTC, strictly after submission/revision time, and strictly before the
mission departure-window start.

RFQ response deadline and quote validity serve different purposes: the operator must submit the
initial quote before the RFQ deadline, while the submitted quote may remain commercially valid
after that deadline as long as it expires before mission departure.

### Revision lineage and current authority

A revision creates a new Quote aggregate and never overwrites the previous commercial terms.

Each revision stores:

- monotonically increasing revision_number;
- supersedes_quote_id;
- immutable prior pricing/terms;
- one authoritative is_current marker.

The prior current quote transitions to SUPERSEDED and emits QUOTE_SUPERSEDED; the replacement emits
QUOTE_REVISED.

PostgreSQL enforces one (rfq_id, revision_number), one child per supersedes_quote_id, and a partial
unique index allowing only one is_current quote per RFQ.

### Expiry and withdrawal

Expiry is permitted only for the current SUBMITTED quote at or after valid_until; it emits
QUOTE_EXPIRED.

Withdrawal is permitted only for the current SUBMITTED quote before valid_until; it emits
QUOTE_WITHDRAWN. At or after valid_until, callers must perform the explicit expiry transition
instead of creating ambiguous terminal history.

Both transitions clear authoritative-current status.

### Idempotency and concurrency

Every PR8 mutation requires Idempotency-Key and reuses the existing canonical request hashing and
PostgreSQL advisory transaction lock:

- same key + same canonical body returns the stored result;
- same key + different body returns 409.

Initial submission locks the RFQ row. Because only ACKNOWLEDGED -> QUOTED is valid, concurrent first
submissions serialize and only one can commit.

Revision, withdrawal, and expiry lock the affected quote row and retain optimistic aggregate
version checks. The database uniqueness constraints provide a second integrity boundary for
revision cardinality.

A race test targets the actual single-use transition invariants; it does not assume two business
transitions are mutually exclusive when the state machine would permit them in sequence.

### Events and atomicity

PR8 emits transactional-outbox events:

- QUOTE_SUBMITTED
- RFQ_QUOTED
- QUOTE_REVISED
- QUOTE_SUPERSEDED
- QUOTE_WITHDRAWN
- QUOTE_EXPIRED

State and events are committed in the same database transaction.

## API

PR8 exposes:

- POST /v1/rfqs/{id}/quotes
- GET /v1/rfqs/{id}/quotes
- GET /v1/quotes/{id}
- POST /v1/quotes/{id}/revise
- POST /v1/quotes/{id}/withdraw
- POST /v1/quotes/{id}/expire

## Non-goals

PR8 does not implement:

- quote normalization, expected/worst-case pricing, or confidence/caveats;
- buyer comparison, review, acceptance, rejection, or award;
- booking creation;
- contracts or payment processing;
- tender/reverse-auction rounds;
- graph projection;
- pricing or supplier ML;
- UI;
- Kafka/Redpanda, Kubernetes, or microservice extraction.
