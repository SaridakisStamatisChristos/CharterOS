# Production-readiness evidence boundary

CharterOS includes the repository-owned **code-capability** required to measure and evaluate production readiness. Repository CI does not create live-production evidence by itself.

See also [candidate SLOs](slo-definition.md), [alert matrix](alert-matrix.md), [load/soak plan](load-test-plan.md), and the [production-observability runbook](../../runbooks/production-observability.md).

## Repository-owned readiness capabilities

- bounded-cardinality Prometheus exposition for API, DB pool/transactions, award, outbox, graph, optimizer, and evidence paths;
- persisted outbox/graph lag state derived from canonical PostgreSQL on scrape;
- executable alert rules for application-owned signals;
- runbook mappings for required alert classes;
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

Generate those in the target environment, preserve the source SHA/release-manifest identity, and archive them with the corresponding release evidence.

A green repository CI run demonstrates that the software can measure and verify these conditions under the tested environment. It does not mean the conditions have been observed in production.
