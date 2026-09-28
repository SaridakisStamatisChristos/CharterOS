# ADR 0011: Atomic Quote Award and Minimal Booking Creation

## Status

Accepted for Roadmap PR11.

## Context

PR10 provides read-only, explainable buyer decision support. It deliberately does not award or
accept a quote. PR11 introduces the first transactional buyer award boundary and must guarantee that
an explicit acceptance cannot produce duplicate winners, duplicate bookings, partial state, or an
implicit dependency on comparison rank.

The established CharterOS rules remain authoritative: PostgreSQL is canonical, money stays exact,
foreign keys use \`RESTRICT\`, aggregate versions are monotonic, mutation endpoints are idempotent,
and domain events enter the transactional outbox in the same transaction as domain state.

## Decision

### Explicit command boundary

\`POST /v1/quotes/{quote_id}/accept\` is the only PR11 award command. It requires an
\`Idempotency-Key\`. No comparison score, rank, currency cohort, or other PR10 output is read by the
award service. A buyer/system command identifying the quote is authoritative.

The endpoint returns the newly created Booking. Replaying the same idempotency key returns the same
Booking without duplicating state or events. A later award attempt with another key conflicts after
the mission has been selected.

### Award serialization and lock order

The Mission row is the serialization point for one award boundary. Acceptance locks the Mission
before locking the mission's current quote rows. Quote submission and quote revision also lock that
Mission before creating a new current quote. This closes the race in which a quote could otherwise
be submitted or revised immediately after an award snapshot.

Acceptance then locks all current quotes for RFQs of that Mission in deterministic
\`rfq_id, quote_id\` order.

The practical order is:

1. Resolve the requested quote and its RFQ without mutation.
2. Lock the Mission.
3. Verify the Mission is still \`SOURCING\` or \`QUOTED\` and has no Booking.
4. Load the Mission's RFQs.
5. Lock all current quotes deterministically.
6. Verify the requested quote is still the current submitted quote, its RFQ is \`QUOTED\`, and it is
   still commercially valid.
7. Create one Booking.
8. Mark the selected quote \`ACCEPTED\`.
9. Mark all other still-current quotes for the Mission \`REJECTED\`.
10. Progress the Mission to \`SELECTED\`.
11. Persist Booking, Quote, Mission, idempotency record, and outbox events in one database
    transaction.

A unique Booking constraint on \`mission_id\` is a database backstop for the one-winner invariant;
\`accepted_quote_id\` is unique as well.

### Losing quote policy

Only current submitted alternatives are closed by an award. They become explicitly \`REJECTED\` and
emit \`QUOTE_REJECTED\` referencing the accepted quote. Historical \`SUPERSEDED\`, \`WITHDRAWN\`,
and \`EXPIRED\` revisions are immutable and are not rewritten.

The winning quote becomes \`ACCEPTED\`, is no longer current, records \`accepted_at\`, and emits
\`QUOTE_ACCEPTED\` referencing the Booking.

### Mission progression

The pre-PR11 implementation can have valid quotes while the Mission is still \`SOURCING\`. To
preserve the documented lifecycle, an award from \`SOURCING\` records:

\`SOURCING -> QUOTED -> SELECTED\`

inside the same transaction. A Mission already at \`QUOTED\` moves directly to \`SELECTED\`.

### Minimal Booking scope

PR11 creates only the state required to anchor the award:

- mission
- accepted quote
- operator
- aircraft
- creation time
- \`PENDING_CONTRACT\` state
- optimistic aggregate version
- \`BOOKING_CREATED\` event

PR12 owns contract semantics. PR13 owns the full Booking workflow. PR11 therefore does not implement
contract acceptance, payment, confirmation, operations, cancellation, or reconciliation.

### Atomicity and event boundary

\`QUOTE_ACCEPTED\`, any \`QUOTE_REJECTED\` events, Mission selection events, \`BOOKING_CREATED\`,
the Booking row, aggregate state changes, and the idempotency response are committed by the same
SQLAlchemy/PostgreSQL transaction. Any exception rolls the entire award back.

No worker or external message broker is introduced. The existing transactional outbox remains the
event boundary; production outbox delivery infrastructure remains Roadmap PR14.

## Database constraints

Migration \`0009_quote_acceptance_booking\`:

- adds \`accepted_at\` and \`rejected_at\` to quotes;
- extends the quote lifecycle constraint with \`accepted\` and \`rejected\`;
- creates \`bookings\` with \`RESTRICT\` foreign keys;
- enforces one Booking per Mission and one Booking per accepted Quote;
- constrains PR11 Booking state to \`pending_contract\`.

Downgrade refuses to erase an established accepted/rejected award history. This is intentional:
audit history is preferred over a destructive schema rollback.

## Consequences

The award boundary is deterministic, explicit, retry-safe, and reconstructable. Concurrent
acceptance attempts serialize to one winner. A successful award leaves no competing current quote
for the Mission, and no quote can be submitted or revised after selection through the supported
application paths.

PR10 remains decision support only. Cross-currency ranking is still unavailable and no implicit FX
conversion is introduced. The auditable FX policy remains deferred to Roadmap PR26.
