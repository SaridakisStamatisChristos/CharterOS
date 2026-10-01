# Production observability runbook

## API error rate

Check status-class and route-template metrics, then correlate with structured logs using correlation
IDs. If database failures dominate, follow the database section. Do not retry non-idempotent
operations blindly.

## Database unavailable

Check PostgreSQL reachability, pool checkout failures, transaction failure class, and current
deployment health. If a commit outcome may be ambiguous, use the existing canonical
idempotency/reconciliation path; never replay an award merely because the client saw a 503.

## Pool saturation

Inspect `charteros_db_pool_checked_out`, configured pool size/overflow, checkout latency, and
transaction duration. Find long transactions or excess concurrency first. Increasing pool size
without database capacity evidence can amplify failure.

## Deadlocks and retry exhaustion

Review bounded retry counters and deadlock/lock-wait metrics. Preserve established lock ordering and
idempotency. Capture the conflicting workflow and SQLSTATE in structured logs; do not add unbounded
SQL text as a metric label.

## Outbox lag

Check pending/retry/in-flight/poison counts and oldest pending age. Confirm worker process health and
downstream publisher health. Expired leases are reclaimed by the existing lease protocol.

## Poisoned outbox event

Investigate the stored bounded error and root cause. Requeue only with the explicit
`--requeue-poison` operator command after the dependency is healthy. Consumer receipts remain the
dedupe authority.

## Graph verification

Do not activate a projection that failed deterministic verification. Quiesce writers/workers as
required, rebuild a new version from canonical outbox history, verify its digest, then explicitly
activate it.

## Graph lag

Compare active projection checkpoint time with canonical event flow and outbox lag. Treat graph as
derived state; never repair canonical history to make graph lag disappear.

## Evidence integrity

Stop evidentiary export/certification claims for the affected environment. Preserve database and
release evidence, run the recovery/evidence verifier, and investigate before any mutation.

## Container restart loop

Use the deployment platform's restart/termination metrics and logs. Check readiness failures,
resource limits, OOM/termination reason, DB dependency, and release digest. PR48 does not assume a
specific orchestrator.

## Disk/storage pressure

Use the deployment platform/PostgreSQL storage metrics. Protect database durability/WAL and evidence
retention. Do not delete immutable evidence or relax governance retention to clear disk pressure.

## Metrics exposure boundary

`/metrics` exposes aggregate operational data only; it contains no tenant or object identifiers.
Production ingress should restrict this endpoint to the monitoring network or authenticated
monitoring plane. The application-level cardinality contract is still enforced even when network
access is restricted.
