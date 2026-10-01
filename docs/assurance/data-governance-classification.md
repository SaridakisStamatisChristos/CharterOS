# CharterOS Data-Governance Classification — PR45

Policy version: `data-governance-v1`

## Scope and authority

This document classifies the data currently persisted by CharterOS and defines the engineering authority for retention, tenant closure, erasure, export, and legal hold. It is an engineering policy, not a claim of GDPR or other legal certification.

PostgreSQL remains the canonical operational source of truth. Charter Graph state is derived and rebuildable. No PR45 path weakens an existing `RESTRICT` foreign key or the evidence-integrity triggers introduced before PR45.

Operator procedure: [commercial data-governance runbook](../runbooks/data-governance.md).

## Classes

| Class | Meaning |
| --- | --- |
| A | Immutable transactional/evidence record |
| B | Tenant/business master data |
| C | Personal-data-capable field or surface |
| D | Operational telemetry/log state |
| E | Derived/rebuildable state |
| F | Security/audit data |
| G | Legal-hold/governance control-plane state |

A table can belong to more than one class. `C` means a field can contain data about a natural person; it does not assert that every row is personal data.

## Current personal-data surface

CharterOS does **not** currently have a dedicated natural-person profile/contact table. Personal data can nevertheless appear in business/free-form surfaces, including legal or trading names for sole traders, mission `special_requirements`, quote terms/conditions, contract references/metadata, disruption reasons/notes, invoice references and reasons, tender correction values/reasons, event payloads, decision evidence snapshots, and deployment-owned application/authentication logs.

Because canonical outbox/evidence payloads can embed those values, deleting a current master row does not necessarily erase historical identity content. PR45 therefore refuses to present master-row deletion as equivalent to legal erasure.

## Retention categories

The executable policy is `charteros.application.data_governance.DATA_ASSET_POLICIES`. CI tests require every ORM table to be classified. The essential policy groups are:

| Assets | Classes | Policy |
| --- | --- | --- |
| organizations, operators, aircraft | B (+ C where applicable) | close/review; erasure only when dependency-free |
| missions, RFQs, quotes, bookings, contracts, tenders, approvals, disruptions, reconciliation, FX locks/conversions, fleet history, matching reference profiles | A (+ C where applicable) | preserve |
| decision evidence, protected financial revisions/decisions, tender corrections, FX rates | A/F (+ C where applicable) | append-only/preserve |
| outbox events, consumer receipts | A/F (+ C for event payloads) | preserve; required for event/deduplication evidence |
| evidence integrity entries/checkpoints | A/F | append-only; never lifecycle-delete |
| idempotency records | D/C/F | policy TTL; automated deletion allowed |
| API rate-limit windows | D/F | policy TTL; automated deletion allowed |
| Charter Graph projection metadata/nodes/edges/cursors | E (+ C where copied) | derived/rebuildable purge allowed |
| legal holds | F/G/C | append/release-only control state |
| governance lifecycle operations/events | A/F/G | append-only evidence |
| application/authentication logs | D/F/C | external platform retention; excluded from tenant export |

Only `idempotency_records` and `api_rate_limit_windows` are authorized for unattended TTL deletion. The PR44 cleanup command now asserts that classification before executing deletes.

## Tenant dependency graph

Buyer organization dependencies are explicitly inspected before erasure, including missions, procurement approvals, contracts, FX locks, financial reconciliations, disputes/variance approvals, buyer disruption decisions, operator profiles attached to the organization, and canonical organization outbox history.

Operator dependencies include RFQs, bookings, capacity reservations, contracts, disruption proposals, reconciliation, tender invitations, quotes through operator aircraft, fleet position/availability history, canonical organization/operator/aircraft events, and any buyer-side use of the operator's organization identity.

These checks are deliberately redundant with PostgreSQL `RESTRICT` constraints. Application checks produce an auditable report; PostgreSQL remains the final referential-integrity authority.

## Closure, erasure, and anonymization

**Closure** is a business lifecycle action, not erasure. Buyer organizations are set inactive. Operator closure sets both the operator commercial status and its owning organization inactive. The transition emits ordinary canonical domain events plus an immutable governance operation/event.

**Erasure** is a narrowly permitted destructive action. A tenant-scoped advisory lock serializes lifecycle operations. The service enumerates protected dependencies and active legal holds before deleting anything. If any blocker exists, it records a `blocked` lifecycle operation and commits no partial deletion. If no blocker exists, only derived graph state and dependency-free master rows are deleted in an explicit order. No blanket `CASCADE` exists.

**Anonymization/pseudonymization:** PR45 does not rewrite immutable evidence, event payloads, quote economics, or integrity-ledger payloads. Where canonical evidence contains identity-bearing content, the correct engineering result is retention/restriction (or a future legally specified detached-identity design), not silent historical rewriting. Current master data can only be physically erased when the dependency scan proves that no retained evidence refers to it. This policy prevents a misleading “anonymized” state that would leave the original value in canonical evidence.

## Durable lifecycle idempotency

Closure/erasure uses a dedicated governance operation ledger rather than relying on the transient 90-day HTTP replay cache. The raw idempotency key is SHA-256 digested before storage. A repeated request with the same key and canonical request hash returns the prior outcome; reuse with a different request conflicts. This remains durable for as long as the governance evidence is retained.

## Legal hold

Legal holds are explicit tenant-scoped records with creation/release evidence. One active hold is allowed per tenant scope. The scope and creator/releaser identities are recorded as digests. A hold can transition only from active to released; it cannot be deleted or rewritten. An active hold blocks tenant erasure.

Transient API rate windows and idempotency replay records are not tenant legal-hold scope: they are non-authoritative bounded operational state and intentionally do not persist raw tenant identity.

## Deterministic tenant export

The tenant export API is authenticated, capability-gated, and resource-scoped to the path tenant. It returns a versioned schema with:

- tenant master data appropriate to the tenant kind;
- bounded mission evidence reconstructed by the existing `EvidenceService`;
- source provenance;
- explicit excluded security/internal surfaces;
- a deterministic SHA-256 verification digest over canonical export content.

The export first runs the existing evidence-integrity verifier in a repeatable-read, read-only transaction. It refuses silent truncation: if the configured mission or event bound cannot produce a complete export, the request fails instead of returning a partial package. It never returns raw database dumps, bearer/JWKS material, rate-limit internals, idempotency internals, security logs, or the raw evidence-integrity ledger.

## Authorization boundary

Governance policy, legal holds, closure, and erasure are administrator capabilities. Tenant export is available only to an authorized buyer/operator for its own path-scoped tenant ID, or to a tenant administrator. Path scope is validated against verified principal memberships; caller-supplied IDs alone never grant access.

## Failure and transaction semantics

All lifecycle mutations run in one PostgreSQL transaction. Any failure rolls back master-state changes, governance reports, and governance evidence together. The existing transaction-failure model remains authoritative; PR45 does not create a second transaction runner or weaken ambiguous-commit semantics.

## Non-goals

PR45 does not claim legal compliance, decide statutory retention periods, delete immutable commercial evidence, export security telemetry, introduce a user/person identity product model, or implement PR46 disaster recovery. Organization-specific legal retention schedules must be supplied by qualified governance/legal stakeholders and can be mapped onto these explicit engineering controls.
