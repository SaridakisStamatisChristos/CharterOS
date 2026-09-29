# ADR 0023: Explicit Disruption Model

- Status: Accepted
- Date: 2026-09-29
- Scope: Roadmap PR23 only

## Context

CharterOS already has canonical Mission, RFQ, Quote, Contract, Booking, fleet timeline,
Tender, transactional-outbox, and buyer/operator portal authorities. Operational
disruptions occur after a Booking exists and require evidence for detection, replacement
or remediation, changed commercial terms, buyer decisions, and final operational
resolution.

Representing these changes as Booking notes, mutable JSON, rewritten accepted Quotes, or
direct edits to Booking operator/aircraft fields would destroy historical commercial and
operational lineage. Reusing the PR22 ProcurementApproval would also conflate its
pre-award contract with a materially different post-booking decision.

## Decision

PR23 introduces an explicit canonical **Disruption** aggregate in PostgreSQL, identified
by **DisruptionId** and tied to exactly one existing **BookingId**.

Supported disruption types are: delay, aircraft_unavailable, crew_unavailable,
airport_restriction, technical, weather, and other.

The lifecycle is explicit:

**open -> proposed / awaiting_buyer -> buyer_approved | buyer_rejected -> resolved**

Resolved is terminal. A later independent operational problem receives a new
DisruptionId.

### Booking authority

Booking remains authoritative for the booked commitment. PR23 never directly rewrites
accepted_quote_id, operator_id, aircraft_id, or Booking lifecycle state to make a
disruption appear resolved. Resolution records the Booking state observed at resolution
and the exact disruption evidence selected.

If a future workflow requires an actual canonical Booking transition, it must use an
existing or deliberately extended Booking application service rather than table updates.

### Replacement proposal lineage

Replacement/remediation proposals are immutable revisions. At most one proposal revision
is current for a disruption. A replacement revision identifies the proposal it
supersedes; historical proposals remain persisted.

A proposed aircraft is validated against canonical fleet ownership and active aircraft
state. When changing aircraft, PR23 also requires authoritative bitemporal availability
at the proposed operational time using proposal time as the knowledge cutoff.

PR23 deliberately refuses a proposed operator different from the Booking operator.
CharterOS does not yet have a post-award re-procurement authority, so accepting another
operator here would create a hidden second award path. Such a change must go through a
future explicit canonical procurement/commitment workflow.

### Commercial-change evidence

An accepted Quote is terminal by design and is never revised by PR23. Instead, PR23
stores immutable disruption-commercial adjustment evidence linked to the Disruption,
exact current replacement proposal, original accepted Quote, existing quote-normalization
version, original normalized expected and worst-case totals, signed known and conditional
minor-unit adjustments, resulting totals, and an optional changed-terms summary.

Totals use the existing deterministic **normalize_quote()** authority and exact signed-int64
minor-unit Money. There is no second generic Quote table or normalization formula.

Commercial revisions form an immutable supersession chain. A buyer decision tied to an
older commercial revision becomes stale.

### Buyer decision

Post-booking disruption decisions are separate from PR22 ProcurementApproval.

A material replacement or any disruption commercial change requires an explicit buyer
decision. Buyer evidence is immutable, timestamped, tied to the exact current proposal
and exact current commercial revision, and emitted as a domain event.

A stale proposal or stale commercial revision cannot be approved. At most one decision
may be persisted for the same exact evidence key; changing the evidence requires a new
proposal or commercial revision. No silent auto-approval is permitted.

### Resolution

Resolution identifies the selected current proposal, selected current commercial evidence
if any, selected buyer approval if required, resolution timestamp and outcome, and
canonical Booking state observed at resolution.

A material resolution cannot complete after buyer rejection or without an approval bound
to the exact current evidence. Optimistic aggregate versioning and row locking serialize
competing decisions and competing terminal resolutions.

### Party isolation

PR23 accepts explicit buyer or operator application context, consistent with PR21/PR22.
These headers are context selectors, not a claim of production authentication.

Buyer reads/decisions require canonical Mission ownership for the affected Booking.
Operator reads/mutations require the canonical Booking operator. Cross-party access fails
closed.

### Tender confidentiality

A Booking may have originated from a Tender, but PR23 operates only on evidence required
for the winning Booking. Its API and service do not enumerate Tender invitations,
competitor Quotes, losing bids, or sealed-bid topology. The original accepted Quote is the
only commercial Quote authority used for disruption normalization.

PR17 confidentiality therefore remains authoritative and PR23 does not create a
post-award competitor-evidence side channel.

### Graph and optimization boundary

The Charter Graph remains an event-derived projection and is not used as canonical
disruption storage. PR23 writes no graph projection rows.

PR23 v1 does not make PR18 stochastic and does not use graph evidence as a substitute for
canonical fleet ownership/availability. Future graph-assisted replacement discovery must
still revalidate canonical state and preserve knowledge-time rules.

### Events and transactional outbox

Every material PR23 mutation records immutable events on the Disruption aggregate and
persists them through the existing transactional outbox in the same database transaction.

The event lineage reconstructs disruption opening, proposal creation/supersession,
commercial-change creation/supersession, buyer approval/rejection, and terminal
resolution.

Existing stable event_id, aggregate version, actor, correlation, canonical JSON,
at-least-once delivery, retry, poison, and consumer-deduplication semantics remain
unchanged. PR23 does not claim the later PR25 evidence-packaging layer.

### Idempotency and concurrency

Every mutating HTTP endpoint uses the established idempotency repository and advisory
transaction lock. Repository writes use row locking or optimistic versions where
authority can race.

This prevents duplicate retry mutations, contradictory buyer decisions for the same exact
evidence, two authoritative terminal resolutions, and partial persisted state after a
failed replacement or commercial change.

### FX boundary

PR23 performs no FX conversion. A disruption commercial adjustment must use exactly the
currency of the accepted Quote. Cross-currency adjustments fail explicitly.

PR26 remains the final numbered roadmap implementation PR and is the only place where
auditable FX conversion will be introduced.

## Persistence

Migration **0016_disruption_model** adds disruptions, disruption_proposals,
disruption_commercial_changes, and disruption_buyer_decisions.

All relational foreign keys use ON DELETE RESTRICT. Partial unique indexes protect current
proposal/commercial revision authority. The downgrade refuses to destroy persisted
disruption evidence.

## Consequences

The model preserves original Booking and Quote history while making operational recovery
fully explicit and replayable. It adds storage and workflow complexity, but that
complexity is intentional: post-booking changes can no longer be represented as
untraceable edits.

Cross-operator recovery remains deliberately unsupported as an award action in PR23.
That limitation is safer than inventing an unreviewed replacement procurement authority.

## Out of scope

PR23 does not implement PR24 invoice reconciliation/disputes, PR25 full evidence
packaging, PR26 FX, production ML, a second Booking lifecycle, a second award authority,
graph-as-canonical disruption state, or arbitrary cross-operator reassignment.
