# Alert matrix

The executable rules live in `deploy/observability/prometheus-alerts.yml`. Thresholds are initial
operating candidates and must be calibrated with staging evidence.

| Alert | Meaning | Initial threshold | Severity | Required action |
| --- | --- | --- | --- | --- |
| API error rate | server-side API failures are sustained | >5% 5xx for 10m | page | inspect DB/app failures, recent release, and saturation before retry amplification |
| Database unavailable | repeated connectivity failures | >=3 in 5m, sustained | page | stop destructive/manual retries; verify PostgreSQL availability and ambiguous-commit reconciliation |
| Pool saturation | callers are consuming nearly all configured DB capacity | >90% for 5m | ticket | inspect long transactions, slow queries, request concurrency; do not blindly enlarge pool |
| Deadlocks/retry exhaustion | transaction contention is recurring | >=3 deadlocks in 10m | ticket | identify lock order/contending workflow and preserve retry/idempotency invariants |
| Outbox lag | durable events are not draining | oldest pending >120s for 5m | page | check worker health, DB, downstream publisher; preserve event order and dedupe receipts |
| Poisoned outbox | delivery exhausted bounded attempts | any poisoned event | page | inspect root cause; requeue only through explicit operator workflow |
| Graph verification | persisted projection disagrees with deterministic replay | any verification failure | page | quiesce activation, rebuild a new version, verify before activation |
| Graph lag | active derived state is stale | >120s for 5m | ticket | inspect outbox/graph consumer throughput and DB pressure |
| Evidence integrity | append-only evidence chain failed verification | any failure | page | stop evidentiary export/claims; preserve database and investigate corruption |
| Container restart loop | deployment repeatedly restarts CharterOS | platform-specific equivalent of >=3 restarts/10m | page | inspect termination reason, health/readiness, memory and DB dependency |
| Disk/storage pressure | platform storage approaches unsafe threshold | platform-specific >=85% used | ticket/page by growth rate | protect PostgreSQL/WAL/log retention first; expand or clean only per runbook |

Container restart and storage pressure require deployment-platform metrics. PR48 intentionally does
not invent Kubernetes or cloud-specific metrics in application code.
