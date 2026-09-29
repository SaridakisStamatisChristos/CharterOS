# ADR 0021 — Operator Portal APIs

## Status

Accepted for Roadmap PR21.

## Context

CharterOS already has canonical Aircraft/Fleet, Mission/RFQ/Quote, Tender, Booking,
transactional-outbox, Charter Graph, repositioning, and historical-intelligence
authorities. PR21 must provide an operator-facing API without creating a second source
of truth or turning presentation-layer identity headers into a claim of production
authentication.

## Decision

PR21 adds `/v1/operator-portal/...` as an operator-scoped application/API composition
layer. Every request carries `X-Operator-Id`. That header is an explicit operator
context/capability selector only; deployment authentication, credential validation,
and RBAC remain external production concerns. The API never describes the header by
itself as authentication.

Cross-operator resources fail closed. Fleet, RFQ, Quote, Booking, mission-calendar,
and empty-leg reads are scoped in the database query or in the upstream typed graph
operation before pagination/optimization. A caller cannot widen scope with generic
filter expressions or an arbitrary query language.

### Fleet and availability

Fleet list/detail reads use canonical `operators`, `aircraft`, `aircraft_types`, and
`airports`. Portal aircraft creation delegates to `CatalogService`; no second aircraft
registry exists.

Availability mutation delegates to `FleetTimelineService`. The existing bitemporal
timeline remains authoritative: half-open validity intervals, event/recorded time,
explicit correction through `supersedes_id`, and no-hindsight `known_as_of` reads are
preserved. The portal does not materialize a lossy mutable "current availability"
table.

### RFQ inbox

The RFQ inbox is a bounded keyset-paginated read model over canonical RFQ and Mission
state. Ordering is deterministic (`created_at DESC, id DESC`). It exposes route,
departure-window, passenger, status, and the operator's own current-Quote summary,
but not buyer budget or unrelated competitive evidence.

Tender-origin RFQs retain PR17 capability semantics. Inbox rows can state that Tender
capability is required, but the persisted invitation identifier is not disclosed by
the inbox. Tender RFQ detail and mutation require a matching
`X-Tender-Invitation-Id` in addition to the matching operator context.

### Quote workflow

Normal RFQ Quote submission/revision/withdrawal delegates to `QuoteService`. Tender
bids delegate to `TenderService`, including invitation acceptance/decline,
initial-bid, ordinary-revision, best-and-final, and withdrawal rules. This preserves
immutable revision lineage, current-Quote uniqueness, validity windows, exact
minor-unit Money, explicit Currency, fee semantics, aircraft ownership validation,
sealed-bid confidentiality, mutation idempotency, and existing outbox events.

The portal exposes a scoped "own Quote" read so an invited operator can inspect its
own sealed-Tender bid without weakening the generic PR17 competitive-evidence gate.

### Mission calendar and Booking status

The mission calendar and Booking APIs are read models over canonical Booking +
Mission + accepted Quote + RFQ + Aircraft lineage. The read model validates that the
Booking operator, RFQ operator, Quote aircraft, Mission, and Aircraft ownership agree;
contradictions fail closed.

Calendar windows use UTC-aware half-open overlap semantics and bounded keyset
pagination. No `Flight` aggregate is fabricated.

PR21 intentionally adds no portal-specific Booking state machine and no new Booking
transition endpoint. Existing Booking workflow authority remains unchanged.

### Empty-leg visibility

Portal empty-leg visibility reuses the active PR16 Charter Graph and PR18 optimizer.
The PR16 graph query gains an optional operator filter that is applied before result
limiting. PR18 accepts the same optional operator scope and therefore computes
canonical revalidation, opportunities, economics, and deterministic assignment only
from that operator's structural windows.

Responses state the evidence boundary:

- PR16 output is structural empty-leg evidence.
- PR18 output is deterministic economics/assignment recommendation evidence.

The portal does not relabel structural candidates as guaranteed feasible/profitable
and does not duplicate optimizer logic. The active graph knowledge cutoff and PR18
historical/no-hindsight check remain authoritative.

### Read consistency and bounds

Coherent portal reads run in PostgreSQL `REPEATABLE READ, READ ONLY` transactions.
List sizes are bounded to 100 rows (fleet/RFQ/Booking/calendar) and use deterministic
keyset cursors. Fleet timelines retain the existing 500-record bound. Mission-calendar
windows are bounded to 366 days. PR16/PR18 retain their existing 90-day and candidate
bounds.

### Money and FX

PR21 performs no FX conversion. Original Quote currency and exact integer minor-unit
amounts remain authoritative. No global cross-currency ranking is introduced.
Roadmap PR26 remains the only task authorized to add auditable FX conversion.

### Event, idempotency, and concurrency

Portal mutations call existing application services inside existing transaction
boundaries. They therefore preserve optimistic aggregate versions, row/advisory
locking, stable domain events, and the PR14 transactional outbox. Portal mutation
idempotency scopes include the operator context and resource identity, preventing
cross-operator replay collisions.

No direct persistence update is used to bypass domain mutation logic.

## Alternatives rejected

1. **Portal-specific Fleet/RFQ/Quote/Booking tables.** Rejected because they create
   conflicting authority and replay/audit ambiguity.
2. **Client-side operator filtering.** Rejected because confidentiality cannot depend
   on UI behavior.
3. **Using generic Quote endpoints for Tender bids.** Rejected because PR17 explicitly
   blocks that path to preserve sealed-Tender semantics.
4. **Copying PR18 economics into portal code.** Rejected because it would create a
   second optimization policy.
5. **Treating `X-Operator-Id` as production authentication.** Rejected because the
   repository does not yet provide that credential-verification boundary.
6. **Adding FX to make portal prices comparable.** Rejected; PR26 is deliberately last.

## Verification

PR21 regression coverage must prove:

- cross-operator Fleet, availability, RFQ, Quote, and Booking isolation;
- operator/Tender invitation capability mismatch fails closed;
- deterministic bounded inbox/list/calendar behavior;
- portal mutations retain idempotency and canonical Quote revision lineage;
- sealed Tender own-bid access does not expose competitor evidence;
- calendar lineage is canonical and preserves UTC/half-open timing;
- empty-leg visibility remains explicitly structural vs optimized and operator scoped;
- original currency is preserved with no implicit FX;
- the complete pre-PR21 suite remains green.

## Roadmap boundary

PR21 does not implement Buyer Procurement APIs (PR22), disruption (PR23), expanded
reconciliation (PR24), the dedicated audit/evidence layer (PR25), production ML, or
auditable FX (PR26).
