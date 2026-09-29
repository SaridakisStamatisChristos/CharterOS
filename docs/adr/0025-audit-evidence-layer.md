# ADR 0025: Audit / Evidence Reconstruction Layer

## Status

Accepted for Roadmap PR25.

## Context

CharterOS already has canonical PostgreSQL aggregates and the PR14 transactional outbox. The
outbox is the immutable domain-event envelope and preserves aggregate identity/version, event
identity/version, occurrence and recording times, actor, correlation, causation, canonical event
JSON, and operational delivery metadata.

PR25 needs deterministic reconstruction of material decisions without creating a second event
store or a second source of business truth. Most state-changing decisions are already recoverable
from canonical aggregates, immutable child evidence, and outbox events. Two procurement decisions
were different: buyer supplier/RFQ selection and the exact quote-comparison result used immediately
before a ProcurementApproval were computed read models whose exact output was not durable.

## Decision

### Source-of-truth hierarchy

1. Canonical domain/PostgreSQL records remain business truth.
2. PR14 `outbox_events` remains the canonical immutable event envelope.
3. Immutable decision evidence snapshots retain only otherwise-ephemeral computed decision output.
4. PR25 evidence packages are generated read models over those sources. They are not writable
   business state and are not persisted as a competing history.

The new `decision_evidence_snapshots` table is append-only by repository contract. A snapshot is
bound to a material source aggregate, carries an explicit schema version, policy versions,
knowledge-time cutoff when applicable, actor/correlation metadata, canonical JSON, and a SHA-256
content digest. It does not duplicate every domain row.

Supplier-selection snapshots are attached to the RFQs actually issued by the buyer. When the
Mission is still OPEN, the snapshot preserves the matching-v1 result available at that exact
selection time, including the winning aircraft evidence and bitemporal knowledge timestamps.
When no current feasible match is available, the snapshot says so explicitly rather than
manufacturing matching evidence.

Quote-comparison snapshots are attached to the resulting ProcurementApproval and preserve the
exact quote-comparison policy, matching policy, quote-normalization version, same-currency ranks,
eligibility evidence, normalized totals, aircraft-suitability evidence, knowledge timestamps,
and the selected immutable Quote revision.

### Package contract

The package schema is `audit-evidence-v1`. A package contains:

- subject type and stable subject ID;
- canonical source references and curated facts;
- ordered canonical event lineage;
- existing actor/correlation/causation metadata;
- material decision snapshots;
- policy/version identifiers;
- source-derived canonical cutoff;
- completeness state and diagnostics;
- deterministic SHA-256 integrity digest.

`generated_at` is generation metadata and is deliberately excluded from the integrity digest.
Equivalent source state therefore produces the same canonical evidence content and digest.

### Completeness and integrity

PR25 does not concatenate rows blindly. It verifies:

- unique event IDs;
- canonical event JSON envelope fields against the authoritative outbox columns;
- contiguous aggregate versions for included full streams;
- aggregate row version against terminal event version when the package is complete;
- Booking -> accepted Quote;
- ProcurementApproval -> Quote -> Booking;
- Contract -> Booking;
- Disruption -> Booking and terminal selected proposal/commercial/buyer-decision references;
- FinancialReconciliation -> Booking / accepted Quote;
- immutable invoice line sum -> invoice total;
- final invoice / approval / payable consistency;
- PR23 commercial-change IDs referenced by the PR24 opening event;
- decision-snapshot SHA-256 digests.

Missing or conflicting evidence fails explicitly. A bounded result that exceeds the event limit is
marked `truncated`; it is never labeled complete.

### Historical and knowledge-time reconstruction

Historical decisions are reconstructed from the exact stored event/snapshot evidence. PR25 does
not rerun the current matching or quote-comparison policy and describe that output as a historical
decision. Matching position, availability, and reference-profile knowledge timestamps retained by
the decision snapshot preserve the existing no-hindsight model. PR23 capability evidence and PR24
quote-normalization/commercial-change evidence remain the source for those workflows.

### Actor, correlation, and causation

PR25 surfaces actor, correlation, and causation IDs exactly as stored. Cross-aggregate timelines are
ordered deterministically, but no causal edge is invented merely because two events are temporally
adjacent.

### Party isolation and sealed tenders

Evidence reads require exactly one application party context: `X-Buyer-Id` or `X-Operator-Id`.
These headers remain application context, not a production-authentication claim.

Buyer access is checked against canonical Mission ownership. Operator access to Booking,
Disruption, and FinancialReconciliation evidence requires the canonical Booking operator. Mission
evidence is buyer-only in v1 because a generic operator-wide Mission package could disclose
competitor selection evidence.

For an active sealed Tender, buyer Mission evidence excludes invitation RFQs, associated Quotes,
and the Tender aggregate stream itself rather than risking invitation-topology or bid leakage.
After the sealed phase is over, ordinary canonical visibility applies.

### Raw event boundary

Canonical event JSON is parsed internally for validation and reconstruction, but the API never
returns the raw `canonical_json`. It returns a curated decision-output field with an explicit
allowlist. Arbitrary metadata, provenance blobs, secrets, credentials, and internal delivery state
are not blindly copied to callers.

### Read consistency, order, and bounds

Evidence API reads run under PostgreSQL `REPEATABLE READ, READ ONLY`. Events are stably ordered by
`(recorded_at, event_id)`; source and snapshot serialization is also deterministic. Packages are
bounded to 1..500 events. Truncation is explicit.

### Outbox delivery boundary

Business-event occurrence and message delivery are different facts. PR25 reconstructs business
decisions from the event envelope. It does not infer that an external consumer performed a side
effect merely because outbox delivery metadata says the event was published.

### FX boundary

PR25 preserves original exact signed-int64 minor-unit amounts and currencies. It performs no
conversion, introduces no hidden rate, and creates no synthetic cross-currency global rank. The
evidence snapshot explicitly records when a quote comparison has no global rank.

Roadmap PR26 owns auditable FX and remains the final numbered implementation PR.

### Integrity digest limitation

The SHA-256 digest is a deterministic reproducibility/tamper-detection value over the package
content. It is not a digital signature, trusted timestamp, external notarization, legal
attestation, settlement proof, or live-production certification.

## API

- `GET /v1/evidence/missions/{mission_id}`
- `GET /v1/evidence/bookings/{booking_id}`
- `GET /v1/evidence/disruptions/{disruption_id}`
- `GET /v1/evidence/reconciliations/{reconciliation_id}`

## Migration

`0018_audit_evidence` creates only `decision_evidence_snapshots`. The downgrade refuses to
destroy persisted decision evidence.

## Consequences

PR25 gains reproducible, bounded, subject-aware evidence packages while preserving the established
authority boundaries. Storage growth is limited to material computed decisions rather than copies
of every event/domain row. Historical reconstruction improves prospectively without rewriting old
evidence: pre-PR25 decisions that never persisted a computed snapshot are not retroactively
invented.

## Explicit non-goals

PR25 does not implement production authentication, external notarization, legal signatures,
payment settlement, a second event store, a second Booking/award/Disruption/Reconciliation
authority, production ML, or FX conversion.
