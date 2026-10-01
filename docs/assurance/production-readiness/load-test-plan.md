# Reproducible load and soak plan

The HTTP plan is executable with `tools/http_load_assurance.py`. It records throughput, status
distribution, error rate, p50/p95/p99/max latency, bounded saturation responses, and post-run
correctness probes.

Example against staging:

```bash
python tools/http_load_assurance.py \
  --plan docs/assurance/production-readiness/load-scenarios.template.json \
  --base-url https://staging.example.invalid \
  --output load-results.json \
  --evidence-class staging \
  --source-sha <40-character-release-source-sha>
```

The tender/optimizer/evidence scenarios require the environment variables referenced in the plan.
Prepare disposable staging fixtures; never point contention tests at live customer state.

## Outbox backlog catch-up

1. Quiesce the outbox worker in a disposable staging environment.
2. Generate a known bounded set of domain events through normal APIs.
3. Record pending count and oldest-pending age from `/metrics`.
4. Resume `python -m apps.outbox_worker.main`.
5. Record time until pending/retry reaches zero and poison remains zero.
6. Verify the Charter Graph and consumer receipts after catch-up.
7. Preserve metrics and verification output with the same release/source identity.

Do not insert synthetic outbox rows directly; that would bypass the semantics under test.

## Soak and correctness

Repeat steady API, optimizer, and evidence scenarios for the intended soak window while collecting
Prometheus metrics. A performance result is invalid if post-run correctness probes fail. Award
contention must retain single-award/capacity invariants; outbox catch-up must retain dedupe and graph
verification; evidence reconstruction must retain integrity verification.

No committed result is labeled staging or production unless it was actually executed there.
