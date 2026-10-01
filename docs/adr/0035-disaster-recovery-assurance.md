# ADR 0035 — Deterministic disaster-recovery assurance

**Status:** Accepted  
**Date:** 2026-10-01  
**Decision:** PR46

## Context

CharterOS treats PostgreSQL as canonical truth, while the Charter Graph is a derived projection rebuilt from transactional outbox history. Evidence-integrity streams, aircraft-capacity exclusion, and transactional consumer receipts already protect important correctness properties. Infrastructure backup mechanisms alone do not prove that a restored database is internally consistent or safe to resume.

PR46 therefore adds repository-owned recovery **verification** and derived-state **rebuild** machinery without inventing a second backup system or a second canonical store.

## Decision

1. Keep backup creation and point-in-time restore provider-specific. The repository owns the checks required after the database has been restored.
2. Make the normal recovery-verification path REPEATABLE READ, READ ONLY.
3. Compare the restored Alembic revision with the repository's single Alembic head. A mismatch is a failed recovery gate, not something the verifier silently migrates.
4. Reuse the existing evidence-integrity verifier. Recovery never rewrites integrity entries, checkpoints, or historical source rows to make a damaged restore appear valid.
5. Rebuild Charter Graph only from canonical outbox_events, into an explicit projection version. Rebuild never activates the version automatically.
6. Verify the rebuilt/current graph against the deterministic reference replay and require matching persisted/reference digests.
7. Treat transactional consumer receipts as the deduplication authority for database consumers. An event made eligible for redelivery after restore may run through delivery again, but a surviving receipt prevents the same transactional consumer effect from executing twice.
8. Report expired in-flight leases and poisoned events separately. Expired leases are reclaimable by the existing lease protocol; poisoned events require operator disposition before recovery is considered ready.
9. Independently scan active aircraft-capacity reservations for overlaps even though PostgreSQL's exclusion constraint is the normal write-time authority.
10. Inspect PostgreSQL foreign-key metadata and detect orphaned references across the restored public schema rather than hard-coding a partial table list.
11. Detect canonical/evidence timestamps materially in the future relative to the verification clock. The default tolerance is five minutes and is recorded in the verification snapshot.
12. Emit a versioned machine-readable recovery manifest. RTO is labeled restore_and_verification only when the operator supplies the observed restore start time; otherwise it is explicitly verification_only.
13. Calculate observed data-loss/RPO evidence only when the operator supplies the source system's expected latest canonical event timestamp and the restored database contains a canonical event timestamp. Missing evidence remains null; no success number is fabricated.

## Derived-state authority

The Charter Graph is persisted derived state and is rebuildable from outbox history. Historical pricing intelligence is a deterministic read-only dataset built from canonical relational evidence, and the reposition optimizer computes from explicit query/request inputs rather than a hidden persisted optimizer truth store. PR46 does not create additional persisted pricing or optimizer authority.

## Outbox recovery boundary

For CharterOS database consumers, the consumer effect and outbox_consumer_receipts insertion commit in the same PostgreSQL transaction. A consistent PITR snapshot therefore contains both or neither. Redelivery with the receipt present is a no-op.

This guarantee does **not** automatically extend to arbitrary external consumers. External systems must deduplicate on the immutable event identity or provide an equivalent idempotency contract.

## Rejected alternatives

### Automatically repair a failed restore

Rejected. Rewriting evidence, inventing missing rows, or silently resetting canonical state can destroy forensic information and create historical contradictions.

### Automatically activate a rebuilt graph

Rejected. Activation changes the live derived-state pointer and still requires the existing maintenance-mode activation workflow after verification.

### Treat a green database connection as recovery success

Rejected. Connectivity says nothing about schema compatibility, graph completeness, evidence integrity, poisoned delivery state, reservation overlap, or referential corruption.

### Commit synthetic RTO/RPO values

Rejected. Production recovery objectives require measurements from real drills. The tool records only observed timestamps supplied or measured during the invocation and labels their scope.

## Failure and concurrency semantics

Recovery verification assumes the restored environment is quiesced. The read-only verifier cannot mutate canonical state. Graph rebuild uses the existing projection advisory locking, aggregate cursors, transactional consumer receipts, and deterministic verification. Existing production workers/writers must not be resumed until the recovery manifest is green and any required graph activation has completed.

## Compatibility

PR46 adds no schema migration and does not change quote economics, award authority, booking capacity rules, evidence append-only enforcement, governance retention rules, transactional retry semantics, or API authorization.

## Assurance

The PR46 test suite verifies read-only restore inspection, deterministic graph rebuild from outbox history, reclaim of expired delivery leases, and prevention of duplicate transactional consumer effects after an event is made eligible for redelivery again.
