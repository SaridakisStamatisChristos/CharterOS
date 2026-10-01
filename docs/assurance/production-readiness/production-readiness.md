# Production-readiness evidence boundary

PR48 closes the **code-capability** portion of the PR43-PR48 hardening roadmap. It does not create
live-production evidence by itself.

See also [candidate SLOs](slo-definition.md), [alert matrix](alert-matrix.md),
[load/soak plan](load-test-plan.md), and the
[production-observability runbook](../../runbooks/production-observability.md).

## Code-capability evidence delivered by PR48

- bounded-cardinality Prometheus exposition for API, DB pool/transactions, award, outbox, graph,
  optimizer, and evidence paths;
- persisted outbox/graph lag state derived from canonical PostgreSQL on scrape;
- executable alert rules for application-owned signals;
- runbook mappings for every required alert class;
- candidate SLO definitions and PromQL measurement queries;
- a reproducible HTTP load/soak harness and scenario template;
- post-run correctness probes and result schema;
- CI observability smoke coverage.

## Live/staging execution evidence still required

The following files are intentionally **not fabricated or committed as successful results**:

- `load-results.json`;
- `chaos-results.json`;
- production/staging alert-delivery receipts;
- production SLO attainment reports;
- deployment-platform restart/storage evidence.

Generate those in the target environment, preserve the source SHA/release manifest identity, and
archive them with the release evidence.

A green PR48 means the repository can measure and verify these conditions. It does not mean the
conditions have been observed in production.
