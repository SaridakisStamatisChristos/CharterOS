# CharterOS documentation

This directory is the navigation root for CharterOS technical, operational, and assurance
documentation.

## Start here

- [Architecture overview](architecture/README.md) — current system shape, authority boundaries, runtime
  processes, and scaling posture.
- [System context and container views](architecture/system-context.md) — C4-inspired visual atlas of
  actors, runtime containers, canonical persistence, derived state, and deployment posture.
- [Critical flows](architecture/critical-flows.md) — sequence diagrams for award/capacity commitment,
  outbox projection, ambiguous commit recovery, disaster recovery, and release provenance.
- [Authority and trust boundaries](architecture/authority-and-trust-boundaries.md) — explicit map of
  command authority, trust re-establishment, failure posture, and evidence classes.
- [Glossary](glossary.md) — canonical terminology for domain, temporal, eventing, assurance, and
  operational concepts.
- [Architecture Decision Records](adr/README.md) — durable design decisions from ADR 0001 through ADR
  0037.
- [Authentication and authorization](authentication-authorization.md) — OIDC/JWT verification,
  principals, capability policy, tenant selectors, and public runtime endpoints.
- [Database-enforced evidence integrity](evidence-integrity.md) — append-only evidence protections,
  integrity chains, checkpoints, and recovery boundary.
- [Assurance index](assurance/README.md) — governance classification, production-readiness evidence,
  SLOs, alerts, load plans, and focused hardening evidence.
- [Runbook index](runbooks/README.md) — operator procedures for governance, disaster recovery, release
  provenance, and production observability.

## Operational hardening map

| Area | Design authority | Operator / assurance documentation |
| --- | --- | --- |
| Aircraft capacity / cross-Mission overlap | [ADR 0027](adr/0027-aircraft-capacity-reservations.md) | [PR38–PR42 hardening lineage](assurance/pr38-pr42-aircraft-commitment-hardening.md) |
| Booking termination / capacity release | [ADR 0028](adr/0028-booking-termination-capacity-release.md) | [PR38–PR42 hardening lineage](assurance/pr38-pr42-aircraft-commitment-hardening.md) |
| Replacement-aircraft feasibility | [ADR 0029](adr/0029-canonical-replacement-aircraft-feasibility.md) | [PR38–PR42 hardening lineage](assurance/pr38-pr42-aircraft-commitment-hardening.md) |
| Optimizer input completeness | [ADR 0030](adr/0030-reposition-optimizer-input-completeness.md) | [PR38–PR42 hardening lineage](assurance/pr38-pr42-aircraft-commitment-hardening.md) |
| Award-time feasibility truth gate | [ADR 0031](adr/0031-award-time-aircraft-feasibility-truth-gate.md) | [PR38–PR42 hardening lineage](assurance/pr38-pr42-aircraft-commitment-hardening.md) |
| Transaction failures / ambiguous commit | [ADR 0032](adr/0032-transaction-failure-and-ambiguous-commit.md) | Main README operational/quality sections |
| API abuse/resource bounds | [ADR 0033](adr/0033-api-abuse-resource-bounds.md) | Authentication docs + resource-cleanup command |
| Commercial data governance | [ADR 0034](adr/0034-commercial-data-governance.md) | [Data-governance runbook](runbooks/data-governance.md), [classification](assurance/data-governance-classification.md) |
| Disaster recovery | [ADR 0035](adr/0035-disaster-recovery-assurance.md) | [Disaster-recovery runbook](runbooks/disaster-recovery.md) |
| Release provenance | [ADR 0036](adr/0036-release-supply-chain-provenance.md) | [Release-provenance runbook](runbooks/release-provenance.md) |
| Production observability | [ADR 0037](adr/0037-production-observability.md) | [Production observability runbook](runbooks/production-observability.md), [production-readiness assurance](assurance/production-readiness/README.md) |

## Evidence status

Repository documentation distinguishes implementation capability from live operational evidence.

A green CI run proves repository-owned checks passed for that source revision. It does not by itself
prove a production deployment, production SLO attainment, live alert delivery, a real restore drill,
or a production load/soak result. Those require retained environment-specific evidence.
