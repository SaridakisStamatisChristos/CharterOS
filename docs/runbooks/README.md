# CharterOS runbooks

These runbooks describe controlled operator procedures around the current CharterOS architecture.

| Runbook | Use when |
| --- | --- |
| [Data governance](data-governance.md) | legal hold, tenant closure, dependency-aware erasure, privacy-safe tenant export |
| [Disaster recovery](disaster-recovery.md) | verifying a restored PostgreSQL database, rebuilding Charter Graph, recording observed RTO/RPO evidence |
| [Release provenance](release-provenance.md) | verifying signed release evidence, SLSA/SBOM attestations, base-image/dependency updates |
| [Production observability](production-observability.md) | API/DB/outbox/graph/evidence failure response and metric/alert interpretation |

## Runbook safety rules

- Preserve PostgreSQL as canonical truth.
- Do not manually rewrite evidence history to make a check pass.
- Do not bypass idempotency, outbox fencing, graph verification, or tenant-governance dependency
  ordering.
- Treat derived state as rebuildable and canonical business history as non-reconstructable authority.
- Record the exact release/source identity for environment-specific evidence.
- Do not relabel CI, local, or staging evidence as production evidence.
