# ADR 0012: Provider-Neutral Contract Layer

## Status

Accepted for Roadmap PR12.

## Context

Roadmap PR11 creates exactly one awarded Booking in \`PENDING_CONTRACT\` state. Roadmap PR12 adds
the contract evidence required between award and the later PR13 Booking workflow.

The roadmap requires:

- contract metadata;
- a document reference;
- buyer acceptance;
- operator acceptance;
- version;
- signed timestamps;
- no complex e-signature integration yet;
- a pluggable integration boundary.

The existing CharterOS invariants remain authoritative: PostgreSQL is canonical, mutation
idempotency is mandatory, foreign keys use \`RESTRICT\`, aggregate versions are monotonic, state
transitions are explicit, and domain events are written through the transactional outbox.

## Decision

### Contract is a separate aggregate

PR12 introduces a framework-independent \`Contract\` aggregate rather than embedding mutable
contract fields directly into Booking.

A Contract records:

- \`booking_id\`;
- the canonical buyer organization derived from the Booking's Mission;
- the canonical operator derived from the Booking;
- an opaque \`document_reference\`;
- positive integer \`document_version\`;
- bounded canonical string metadata;
- Contract status;
- creation time;
- buyer signed/accepted timestamp;
- operator signed/accepted timestamp;
- final bilateral acceptance timestamp;
- monotonic aggregate version.

PR12 permits exactly one Contract aggregate per Booking. Contract document revision/replacement is
not silently invented in this PR; the explicit \`document_version\` is persisted so external
document evidence remains versioned, while a future roadmap change may define revision semantics if
the product requires them.

### Opaque, provider-neutral document references

CharterOS treats \`document_reference\` as an opaque stable reference. It may later identify an
object-store document, e-sign envelope, document-management record, or another provider artifact.

The core domain does not parse provider-specific IDs or URLs.

\`ContractDocumentIntegration\` is an application port that can validate an external document
reference when an adapter is injected. The current API does not require an external adapter and
therefore does not introduce a network dependency, vendor SDK, callback/webhook protocol, or
provider-specific canonical state.

This is the deliberate PR12 integration seam.

### Acceptance semantics

The API exposes explicit buyer and operator acceptance commands.

Each party acceptance is single-use and records a server-side UTC timestamp:

- \`buyer_signed_at\`
- \`operator_signed_at\`

These timestamps record **CharterOS acceptance evidence**. They are not a claim that CharterOS has
performed cryptographic digital-signature verification, qualified electronic signatures, identity
proofing, or legal signature validation.

Contract status is deterministic:

\`\`\`text
PENDING_ACCEPTANCE
  -> PARTIALLY_ACCEPTED
  -> ACCEPTED
\`\`\`

The first party acceptance emits its party-specific event. When the second party accepts, the same
transaction also emits \`CONTRACT_ACCEPTED\`. \`accepted_at\` is the later of the two party timestamps.

### Events

PR12 emits:

\`\`\`text
CONTRACT_CREATED
CONTRACT_BUYER_ACCEPTED
CONTRACT_OPERATOR_ACCEPTED
CONTRACT_ACCEPTED
\`\`\`

All events share the existing transactional-outbox boundary with the Contract state mutation.

### Idempotency and concurrency

Every PR12 POST endpoint requires \`Idempotency-Key\` and reuses the established PostgreSQL advisory
transaction lock:

\`\`\`text
same key + same canonical request       -> stored response
same key + different canonical request -> 409
\`\`\`

Contract creation locks the Booking row and is additionally protected by a database unique
constraint on \`booking_id\`.

Contract acceptance locks the Contract row and persists with optimistic aggregate version checking.
Concurrent buyer/operator acceptance serializes safely and may allow both independent party
acceptances to succeed. Concurrent duplicate acceptance by the same party yields exactly one state
transition and one party event; the other attempt conflicts.

### PR13 boundary

A fully accepted Contract remains contract evidence. PR12 deliberately does **not** transition the
Booking from \`PENDING_CONTRACT\` to \`CONTRACTED\`, nor does it advance the Mission beyond
\`SELECTED\`.

Roadmap PR13 owns:

\`\`\`text
CONTRACTED
PAYMENT_PENDING
CONFIRMED
PRE_OPERATION
OPERATING
COMPLETED
RECONCILED
\`\`\`

PR13 can use \`ContractStatus.ACCEPTED\` as an explicit guard when it introduces the Booking
workflow.

### Persistence

Migration \`0010_contracts\` creates \`contracts\` with:

- \`RESTRICT\` foreign keys to Booking, buyer Organization, and Operator;
- one-Contract-per-Booking uniqueness;
- positive aggregate/document version constraints;
- explicit status vocabulary;
- timestamp/status consistency checks;
- buyer/operator/status indexes.

Downgrade refuses to discard Contract rows after either party has accepted, because signed
acceptance evidence is audit history.

## API

PR12 exposes:

\`\`\`text
POST /v1/bookings/{booking_id}/contract
GET  /v1/bookings/{booking_id}/contract
GET  /v1/contracts/{contract_id}
POST /v1/contracts/{contract_id}/accept/buyer
POST /v1/contracts/{contract_id}/accept/operator
\`\`\`

The roadmap did not prescribe exact PR12 endpoint names; these endpoints are the focused REST shape
chosen to express the required capabilities without pulling PR13+ workflow forward.

## Consequences

Contract creation and bilateral acceptance are deterministic, provider-neutral, retry-safe,
concurrency-safe, and reconstructable from canonical PostgreSQL state plus immutable events.

CharterOS gains a clean adapter seam for later e-signature integration without making any external
provider a current source of transactional truth.

Complex e-signature orchestration, contract revisions, payment, Booking state progression,
production outbox delivery, and later roadmap concerns remain out of PR12.
