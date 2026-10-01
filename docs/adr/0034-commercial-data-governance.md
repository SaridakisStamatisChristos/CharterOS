# ADR 0034 — Commercial data-governance authority

**Status:** Accepted  
**Date:** 2026-10-01  
**Decision:** PR45

## Context

CharterOS already protects historical commercial evidence, no-hindsight state, transactional outbox history, and evidence-integrity streams. PR44 added bounded cleanup only for transient idempotency replay rows and API rate windows. A commercial multi-tenant platform also needs explicit retention classification, tenant closure/erasure semantics, legal holds, and tenant export without weakening those durability guarantees.

## Decision

1. Treat PostgreSQL as the canonical governance authority.
2. Classify every persisted ORM table with the A–G policy in `data_governance.py`; CI fails on unclassified additions.
3. Permit unattended TTL deletion only for `idempotency_records` and `api_rate_limit_windows`.
4. Distinguish tenant **closure** from physical **erasure**.
5. Serialize lifecycle operations per tenant with a PostgreSQL advisory transaction lock.
6. Preserve all existing `RESTRICT` foreign keys. Never introduce blanket cascading deletion.
7. Before erasure, enumerate canonical dependencies and active legal holds. A blocker produces an immutable blocked report and no partial deletion.
8. Store closure/erasure outcomes in an immutable, evidence-integrity-protected lifecycle ledger with durable hashed-key idempotency.
9. Represent legal holds as append/release-only records and separately append governance events protected by the integrity ledger.
10. Export only tenant-scoped, bounded, versioned data reconstructed through domain/evidence services; fail on incomplete bounds rather than silently truncate.
11. Do not rewrite immutable evidence to simulate anonymization. If retained evidence contains identity-bearing data, physical erasure is blocked until a legally specified transformation can preserve historical correctness.

## Rejected alternatives

### Blanket `ON DELETE CASCADE`

Rejected because it would turn referential cleanup into silent evidence destruction and could invalidate bookings, awards, reconciliation, outbox/deduplication, and integrity chains.

### Delete tenant master rows and leave orphaned identifiers

Rejected because it breaks referential integrity and can create unverifiable history.

### Raw database dump for DSAR/export

Rejected because it leaks internal security/operational surfaces, exposes schema internals, and makes tenant scoping difficult to prove.

### Treat tenant closure as erasure

Rejected because commercial deactivation and legal data-erasure obligations have different authority and retention semantics.

### Rewrite historical events/evidence during anonymization

Rejected because CharterOS's integrity/no-hindsight model requires historical evidence to remain verifiable. PR45 records the constraint rather than falsifying erasure.

## Concurrency and retries

Lifecycle actions acquire a tenant-specific transaction-scoped advisory lock. Durable lifecycle idempotency is keyed by a SHA-256 digest of the caller's idempotency key plus a canonical request hash. The raw key is never stored. All master mutations and governance evidence commit atomically.

## Compatibility

PR45 does not change award authority, feasibility, quote lineage, capacity exclusion, transaction-retry semantics, outbox delivery, evidence-integrity rules, or existing authentication verification. It extends the deny-by-default route inventory with explicit governance policies.

## Assurance

Tests cover policy inventory, authorized automated deletion surfaces, tenant-scoped export, cross-tenant denial, legal-hold erasure blocking, closure replay, dependency-blocked erasure, dependency-free erasure, immutable governance history, and evidence-integrity verification after governance operations.
