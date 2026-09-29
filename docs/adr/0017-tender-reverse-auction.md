# ADR 0017 — Tender / Reverse Auction v1

## Status

Accepted for Roadmap PR17.

## Context and purpose

CharterOS already has authoritative Mission, RFQ, immutable Quote revision, atomic award, Contract,
Booking, transactional-outbox, and non-canonical Charter Graph capabilities. PR17 adds a bounded B2B
tender / reverse-auction workflow without creating a second procurement truth.

PR17 must support an explicit tender window, invited suppliers, revision participation,
best-and-final (BAFO), sealed-bid confidentiality, award, and an auditable post-deadline correction
mechanism. The decisive invariant is that supplier/commercial bid state cannot be mutated at or after
the authoritative deadline. Closing and awarding are post-deadline lifecycle decisions over frozen
evidence; they do not rewrite supplier submissions. Any later correction to tender evidence is an
append-only administrator correction event.

## Decision

### Tender aggregate and state machine

A `Tender` is a versioned aggregate keyed by `TenderId` and linked one-to-one with a Mission.
Its explicit states are:

```text
DRAFT -> OPEN -> [BEST_AND_FINAL] -> CLOSED -> AWARDED
```

`DRAFT -> OPEN` cannot occur before `opens_at` or at/after `deadline_at`. `OPEN ->
BEST_AND_FINAL` is optional and must occur before the deadline. `OPEN` or `BEST_AND_FINAL` may
become `CLOSED` only at/after the authoritative deadline. `CLOSED -> AWARDED` records the quote
and the canonical Booking created by the existing award workflow.

The database duplicates the important lifecycle-shape and time-order invariants with check
constraints. Application commands take row locks and aggregate versions remain optimistic
concurrency guards.

### Tender window and deadline authority

`opens_at` and `deadline_at` are UTC-aware persisted values. All supplier-side participation paths
check the Tender row under lock. The boundary is strict: `now >= deadline_at` rejects invitation
responses, bid submission, revision, BAFO submission, and bid withdrawal.

Tender-controlled RFQs use the same `deadline_at` as their RFQ response deadline. Existing generic
RFQ and Quote mutation services are tender-aware: when an RFQ is attached to a tender invitation,
they reject direct mutation unless called through the Tender application service. This prevents a
client from bypassing PR17 by calling legacy RFQ/Quote endpoints.

The only post-deadline mechanism that can *correct* pre-deadline evidence is `TENDER_ADMIN_CORRECTED`.
Closing and awarding remain normal lifecycle transitions over immutable/frozen bid evidence.

### Invitation model

An invitation is explicit persisted evidence containing Tender, Operator, and the existing canonical
RFQ. `(tender_id, operator_id)` and `rfq_id` are unique. The invited Operator must pass the existing
RFQ eligibility checks (verified, insured, commercially active). Acceptance and decline are persisted
and recorded as Tender events. Externally retried invitation mutations use the existing API
idempotency ledger and PostgreSQL advisory transaction lock.

An uninvited Operator cannot submit a tender bid: bids are addressed by a persisted invitation and
that invitation must be `ACCEPTED`.

### Sealed-bid visibility

`sealed_bid` is a domain property, not a UI convention. While a sealed Tender is `DRAFT`, `OPEN`,
or `BEST_AND_FINAL`, competitive evidence is fail-closed across the application/API boundary:
generic RFQ Quote listing, Quote detail, normalization, Mission Quote comparison, Charter Graph Quote
history, and the full Tender audit feed reject access. The generic Tender detail also withholds the
invitation topology during the sealed phase, so it does not disclose competitor invitation/RFQ/Quote
identifiers that could be chained into another read surface.

The supplier-facing query returns only one invitation's own RFQ/Quote lineage. Until PR21 supplies
operator-portal authentication/authorization, it requires both the operator identifier and the
unguessable persisted invitation identifier as a capability pair; the application verifies that both
belong to the requested Tender. Possession of an operator ID alone is insufficient. A production
identity provider must ultimately bind that capability to an authenticated operator principal.

After `CLOSED` or `AWARDED`, the sealed phase has ended and the bounded audit/procurement read
surfaces can expose the frozen evidence needed for award review and reconstruction.

### Quote revisions

PR17 does not introduce a tender-specific price object. Initial bids call the existing `QuoteService`
and revisions call `Quote.revise`, producing a new immutable Quote linked through
`supersedes_quote_id` while the previous revision becomes `SUPERSEDED`. The invitation records only
pointers to its latest Quote and, when relevant, its explicit BAFO Quote.

A tender Quote must remain valid beyond `deadline_at`, so a frozen winning bid can still be awarded
after close; existing Quote validation still requires validity to end before Mission departure.

### Best-and-final

BAFO is an explicit Tender state transition and `TENDER_BEST_AND_FINAL_REQUESTED` event. Once the
Tender enters `BEST_AND_FINAL`, ordinary revisions are rejected. A supplier with an existing current
bid may submit one explicit BAFO revision through the existing immutable Quote lineage, producing
`TENDER_BEST_AND_FINAL_SUBMITTED`. If BAFO was requested, award requires the supplier's latest Quote
to be that invitation's explicit BAFO Quote.

### Award and canonical booking authority

PR17 does not create a tender award table that competes with PR11–PR13. `TenderService.award` calls
`BookingService.accept_quote` with Tender context. `BookingService` retains the Mission-row
serialization point, unique Booking constraints, current-Quote locking, winner acceptance, losing
Quote rejection, and Mission selection semantics. Direct generic acceptance of a tender Quote is
rejected, so the Tender cannot be bypassed.

The Tender then records `TENDER_AWARDED` with the accepted Quote and resulting Booking identifiers.
If any operation fails, the request transaction rolls back as a unit.

### Concurrency and idempotency

Tender lifecycle/participation commands lock the Tender row; invitation commands additionally lock
the invitation row. Existing Quote/RFQ locks and optimistic aggregate versions remain in force.
Award additionally uses the PR11 Mission serialization boundary. API mutations use the existing
`Idempotency-Key` ledger and request hash, so exact retries return the stored response while key reuse
with different input fails closed.

### Administrator correction event

Administrator correction is append-only. It never updates or deletes the original Tender, RFQ,
Quote, invitation, Booking, or event record. Each correction persists:

```text
actor_id
corrected_at
target_type
target_id
field_name
original_value
replacement_value
reason
causation_event_id
```

and emits `TENDER_ADMIN_CORRECTED` with the actor in the event envelope and the required causal event
as `causation_id`. It is accepted only at/after the Tender deadline. Before persistence, the
application verifies that the causal event is part of this Tender's bounded evidence graph and that
the correction target aggregate is also part of that graph; no-op corrections are rejected. Therefore
the original evidence and the corrective interpretation coexist and can be reconstructed
independently.

### Audit reconstruction

A bounded Tender audit query returns the Tender, invitations, append-only corrections, and
transactional-outbox event envelopes covering the Tender, its Mission, invited RFQs, their Quote
lineages, and the resulting Booking if awarded. Event envelopes retain `event_id`, aggregate version,
`occurred_at`, `recorded_at`, actor, correlation, causation, and canonical JSON.

### Transactional outbox

Tender is a normal aggregate for event persistence. Tender events use the same
`SqlAlchemyDomainEventRepository` and `outbox_events` table as existing aggregates, so PR14 stable
`event_id`, at-least-once delivery, retry/poison, and durable-consumer semantics are unchanged.

### Charter Graph interaction

PR15 Charter Graph projection v1 deliberately supports only its existing aggregate set. `tender` is a
new unsupported aggregate type and is therefore ignored by projection v1 rather than silently mapped.
Canonical Tender state is PostgreSQL/domain state only. No graph write participates in a Tender
transaction and PR17 does not change PR15/PR16 query authority. A future projection version may add
Tender mappings through the existing build/verify/activate lifecycle.

### FX and ranking

PR17 performs no FX conversion and introduces no cross-currency ranking. The pre-PR26 rule remains:
comparison/ranking is currency-scoped and missing explicit conversion evidence fails closed.

## Non-goals

PR17 does not implement repositioning/deadhead optimization, profitability optimization, ML ranking,
a canonical Flight aggregate, operator-portal authentication, or Auditable FX Policy. PR18 owns
repositioning/deadhead optimization. PR26 remains the final numbered implementation PR for FX.

## Consequences

The Tender capability gains an explicit, reconstructable lifecycle without weakening prior
procurement guarantees. The principal cost is deliberate cross-service guarding: RFQ, Quote, and
Booking mutation services must recognize tender-owned participation so older endpoints cannot bypass
Tender policy. This coupling is preferable to duplicating RFQ/Quote/award authority.
