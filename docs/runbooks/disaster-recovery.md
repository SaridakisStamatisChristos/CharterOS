# CharterOS disaster-recovery runbook

This runbook verifies a database **after** infrastructure restore. Backup creation, snapshot retention, WAL archiving, and provider-specific PITR commands remain deployment responsibilities.

## Preconditions

- Quiesce API writes, outbox workers, graph consumers, scheduled cleanup, and other database writers.
- Record the restore/drill start timestamp in UTC.
- Record the backup/PITR cutoff timestamp when known.
- From the source environment, record the latest canonical outbox_events.recorded_at timestamp before the incident/drill if it is available. This is the evidence needed to measure observed data loss rather than guess an RPO result.
- Restore PostgreSQL into the target recovery environment.
- Do not rewrite evidence rows, integrity chains, or business history to make checks pass.

## 1. Verify the restored database before derived-state rebuild

Example:

~~~bash
uv run python -m apps.recovery_verification.main \
  --database-id recovery-2026-10-01 \
  --backup-cutoff-at 2026-10-01T08:45:00Z \
  --expected-latest-canonical-at 2026-10-01T08:44:58Z \
  --recovery-started-at 2026-10-01T08:40:00Z \
  --manifest recovery-manifest.json
~~~

Omit timestamps that are genuinely unknown. The manifest will leave unsupported RPO evidence unmeasured rather than inventing a number.

The verifier checks the restored Alembic revision, latest canonical outbox timestamp, evidence integrity, outbox delivery state, poisoned events, expired leases, consumer receipts, capacity overlap, foreign-key orphans, future timestamps, and current/selected Charter Graph integrity.

The verification transaction is REPEATABLE READ, READ ONLY.

## 2. Rebuild Charter Graph when required

Choose a new projection version that is not an existing verified/active version, keep writers and workers quiesced, then run:

~~~bash
uv run python -m apps.recovery_verification.main \
  --database-id recovery-2026-10-01 \
  --recovery-started-at 2026-10-01T08:40:00Z \
  --rebuild-graph-version 460001 \
  --maintenance-mode \
  --manifest recovery-manifest.json
~~~

The rebuild replays canonical outbox history using the existing graph projection machinery and verifies the deterministic digest. It does **not** activate the rebuilt graph.

After the manifest is green, activate the verified projection explicitly:

~~~bash
uv run python -m apps.graph_projection.main activate --version 460001 --maintenance-mode
~~~

Then run the recovery verifier once more against the now-active projection.

## 3. Outbox recovery

Expired in-flight leases are safe to reclaim through the existing bounded lease protocol. Do not manually edit lease tokens to force progress.

A non-zero poisoned-event count blocks a green recovery manifest. Investigate each poisoned event and use the existing explicit requeue workflow only after the root cause is understood.

Database consumers use a receipt keyed by consumer identity and immutable event ID. Consumer effect and receipt commit atomically, so safe redelivery does not repeat an authoritative database effect. For external consumers, confirm their independent event-ID/idempotency guarantee before resuming delivery.

## 4. RTO/RPO evidence

rto_seconds has an explicit scope:

- restore_and_verification when --recovery-started-at is supplied from the real drill;
- verification_only when it is omitted.

observed_data_loss_seconds is emitted only when both the source's --expected-latest-canonical-at and the restored database's latest canonical event timestamp are available.

Do not relabel verification-only timing as production RTO, and do not infer RPO from a configured backup schedule.

## 5. Resume criteria

Resume writers/workers only when:

1. the recovery manifest reports ok: true;
2. Alembic revision matches the intended application release;
3. evidence integrity reports zero violations;
4. the selected/rebuilt graph verifies;
5. active capacity overlap is zero;
6. foreign-key orphan count is zero;
7. poisoned outbox events are zero or have been explicitly dispositioned and a fresh manifest is green;
8. any rebuilt graph has been deliberately activated;
9. external consumers have confirmed their redelivery/idempotency contract.

Preserve the recovery manifest and provider restore evidence as drill/incident evidence. Do not commit environment-specific production manifests containing operational identifiers to the source repository.
