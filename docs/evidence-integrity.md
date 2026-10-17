# Database-enforced evidence integrity

CharterOS enforces evidence integrity below the ORM boundary. Application hashes remain useful, but PostgreSQL also rejects unauthorized historical mutation and maintains an independent deterministic integrity ledger for protected evidence.

## Trust boundary

Migrations run as the schema owner/migrator. Production application and worker connections should use a non-owner runtime role configured with `CHARTEROS_DATABASE_RUNTIME_ROLE`. The owner is reserved for migrations, controlled recovery, and forensic operations; routine application traffic must not use it.

`deploy/postgres/evidence_runtime_role.sql` creates the NOLOGIN `charteros_runtime` role and applies the current evidence privilege policy. A DBA grants that role to the actual application login. Re-apply the privilege function after migrations add protected tables.

## Immutable evidence versus lifecycle state

The database protects the immutable portion of each protected row while allowing legitimate lifecycle fields to advance. Examples:

- outbox event identity, aggregate/version, timestamps and canonical payload are immutable; lease, retry, poison and delivery metadata remain mutable;
- FX observations and lock conversion rows are append-only; FX lock terms and digest are immutable while consumption status/version fields may advance;
- live quote and booking rows remain operational records and are not frozen wholesale;
- commitment evidence is protected at contract, procurement-approval, decision-snapshot and immutable outbox-event layers; contract signature/status state may advance;
- reconciliation monetary basis and append-only invoice/dispute/approval evidence are protected while reconciliation lifecycle pointers and completion fields may advance;
- decision snapshots, tender administrative corrections and consumer receipts are append-only.

All protected rows reject DELETE. Pure evidence tables additionally lose runtime UPDATE privilege. Mixed lifecycle tables keep UPDATE permission, but an `ENABLE ALWAYS` trigger compares old and new immutable projections and rejects forbidden changes. Direct SQL is therefore subject to the same historical-integrity boundary as ORM writes.

## Bounded integrity streams

Every protected INSERT is captured in `evidence_integrity_entries`. A stream key is derived from the source table and its natural aggregate/subject identity. Appends take a PostgreSQL transaction-level advisory lock only for that stream, so unrelated missions, RFQs, bookings, reconciliations and outbox aggregates do not share one global serialization point.

Each entry stores deterministic stream sequence, source table and primary-key JSON, the canonical immutable row projection, previous digest, and a SHA-256 digest over stream identity, sequence, lineage and canonical payload. Existing protected rows are backfilled in deterministic domain order before capture triggers are enabled.

## Verification and checkpoints

`charteros_verify_evidence_integrity(stream_key)` verifies sequence continuity, previous-digest lineage, every digest, the current protected projection of each source row, and any checkpoint root. Audit-evidence endpoints invoke this verifier inside the same repeatable-read transaction before reconstructing an evidence package, so privileged historical tampering surfaces as a conflict rather than being silently emitted as valid evidence.

`create_evidence_checkpoint` records the current stream root. Checkpoints are append-only and their root must match a real ledger entry. They are deliberately internal unsigned checkpoints. CharterOS does not claim KMS/HSM signing, external notarization, or third-party anchoring until a real provider is configured and exercised.

## Operational recovery

A schema owner can technically disable triggers for controlled recovery or forensic work. The integrity ledger therefore also verifies source rows: owner-level bypass produces a detectable `source_payload_mismatch`. Tampering with the ledger itself is detectable through digest and previous-digest verification once append-only protections are bypassed by an owner.

Do not grant application logins table ownership or superuser privileges; doing so would collapse the database privilege boundary.

For post-restore verification, graph rebuild, corruption checks, and observed RTO/RPO evidence, use the [disaster-recovery runbook](runbooks/disaster-recovery.md).
