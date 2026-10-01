# ADR 0037 — Production observability and failure evidence

**Status:** Accepted  
**Date:** 2026-10-01  
**Decision:** PR48

## Context

CharterOS already has transactional, recovery, governance, and release-provenance controls, but a
green test suite alone does not show whether production failure modes are visible. PR48 defines a
bounded operational measurement contract without changing business authority.

## Decision

1. Expose Prometheus-compatible metrics from `/metrics`.
2. Keep metric labels bounded and code-controlled. Object IDs, tenant IDs, subjects, aircraft
   registrations, idempotency keys, correlation IDs, and free-form messages remain out of labels.
3. Measure API volume/latency/status/in-flight work plus authentication, authorization, request-bound,
   and abuse-budget rejection.
4. Instrument the SQLAlchemy pool and transaction boundary for checkout pressure, transaction
   duration, retries, deadlocks/lock waits, connectivity failure, and ambiguous-commit reconciliation.
5. Instrument award outcomes with a bounded reason taxonomy rather than exception text.
6. Instrument optimizer workload/runtime/rejections, evidence generation/integrity, outbox
   delivery/dedupe, and graph rebuild/verification.
7. Derive persistent outbox status/oldest age and active graph lag/version from canonical PostgreSQL
   during metrics collection. Metrics never become a second source of truth.
8. Define candidate SLOs and alert thresholds as unvalidated operating targets. They are not claims
   of observed production performance.
9. Provide a reproducible load harness that records throughput, percentiles, errors, saturation
   responses, source SHA, execution environment class, and explicit post-run correctness probes.
10. Keep code-capability evidence separate from live staging/production evidence.
11. Make observability smoke a required signed release gate so PR47 provenance records cannot claim a
    complete release without verifying the PR48 metrics surface.

## Metrics exposure boundary

The endpoint contains aggregate operational state only. Production ingress must restrict it to the
monitoring network or monitoring identity. Network restriction does not replace the cardinality and
privacy constraints in application code.

## Failure semantics

Instrumentation is observational. It must not alter transaction retry, award authority, outbox
delivery, graph replay, evidence integrity, or optimizer completeness. A metrics failure must never
turn a valid business operation into a different decision.

## Platform signals

Container restart loops and disk/storage pressure are deployment-platform concerns. PR48 documents
their required alerts/runbooks but does not introduce Kubernetes or cloud-specific dependencies.

## Evidence status

Repository CI can prove instrumentation wiring and correctness tests. It cannot prove production SLO
attainment, real alert delivery, real capacity saturation, or a live soak. Those require retained
execution evidence from the target environment.
