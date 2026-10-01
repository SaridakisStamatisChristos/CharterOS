# Production-readiness assurance

PR48 provides the repository-owned measurement and evidence framework for production readiness. This
directory does not claim that production SLOs or capacity targets have been achieved.

## Documents

- [Production-readiness boundary](production-readiness.md) — what the code/CI proves and what still
  requires live staging or production evidence.
- [Candidate SLOs](slo-definition.md) — initial measurable targets and PromQL definitions.
- [Alert matrix](alert-matrix.md) — application-owned alert conditions and required operator action.
- [Load / soak plan](load-test-plan.md) — reproducible execution process and correctness requirements.
- [Load-scenario template](load-scenarios.template.json) — executable HTTP scenarios for steady load,
  burst, award contention, optimizer concurrency, evidence reconstruction, and pool pressure.

## Executable assets

- `deploy/observability/prometheus-alerts.yml` — Prometheus alert rules for application-owned
  signals.
- `tools/http_load_assurance.py` — load-evidence runner that records environment class, source SHA,
  latency percentiles, error rate, saturation responses, and post-run correctness probes.

## Missing live evidence by design

The repository does not commit fabricated successful `load-results.json`, `chaos-results.json`,
production alert receipts, production SLO reports, or deployment-platform restart/storage evidence.

Those files become meaningful only when generated in the target environment and retained with the
corresponding release identity.
