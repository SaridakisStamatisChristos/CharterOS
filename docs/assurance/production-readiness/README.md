# Production-readiness assurance

This directory contains the repository-owned measurement and evidence framework for production readiness. It does not claim that production SLOs or capacity targets have been achieved.

## Documents

- [Production-readiness boundary](production-readiness.md) — what code and CI prove, and what still requires live staging or production evidence.
- [Candidate SLOs](slo-definition.md) — initial measurable targets and PromQL definitions.
- [Alert matrix](alert-matrix.md) — application-owned alert conditions and required operator action.
- [Load / soak plan](load-test-plan.md) — reproducible execution process and correctness requirements.
- [Load-scenario template](load-scenarios.template.json) — executable HTTP scenarios for steady load, burst, award contention, optimizer concurrency, evidence reconstruction, and pool pressure.

## Executable assets

- `deploy/observability/prometheus-alerts.yml` — Prometheus alert rules for application-owned signals.
- `tools/http_load_assurance.py` — load-evidence runner that records environment class, source SHA, latency percentiles, error rate, saturation responses, and post-run correctness probes.

## Repository evidence versus live evidence

Repository CI can verify code quality, integration behavior, recovery checks, observability instrumentation, container security, SBOM generation and signed release provenance. That evidence is necessary but not sufficient for a production-readiness claim.

The repository intentionally does not commit fabricated successful `load-results.json`, `chaos-results.json`, production alert receipts, production SLO reports, or deployment-platform restart/storage evidence. Those artifacts become meaningful only when generated in the target environment and retained with the corresponding immutable release identity.
