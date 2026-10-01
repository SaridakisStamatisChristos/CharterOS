# CharterOS architecture

CharterOS is a **production-oriented modular monolith** with PostgreSQL as the canonical
transactional system of record. The architecture keeps business transactions local and explicit
while projection, worker, optimization, evidence, recovery, and release-assurance processes remain
separately operable.

This document describes the current post-PR48 architecture. Durable design details live in the
[ADR index](../adr/README.md).

## Runtime topology

```mermaid
flowchart LR
    U[Buyer / Operator / Admin / Service] --> API[FastAPI API]
    API --> AUTH[OIDC/JWT + route capability policy]
    AUTH --> APP[Application services]
    APP --> DOMAIN[Domain model]
    APP --> PG[(PostgreSQL canonical state)]
    APP --> OUTBOX[(Transactional outbox)]

    OUTBOX --> OW[Outbox worker]
    OW --> GRAPH[(Versioned Charter Graph)]
    OW --> PUB[Structured publisher]

    PG --> MATCH[Matching]
    PG --> OPT[Reposition optimizer]
    PG --> EVID[Evidence reconstruction]
    PG --> PI[Pricing intelligence]

    API --> METRICS[/metrics]
    PG --> METRICS

    CI[CI quality job] --> ART[Container archive + SBOM + evidence]
    ART --> PROV[Isolated provenance job]
    PROV --> ATTEST[Signed build/SBOM/manifest attestations]
```

## Layering

| Layer | Responsibility |
| --- | --- |
| `charteros/domain` | pure aggregates, value objects, invariants, exact money/time rules |
| `charteros/application` | use-case orchestration and authority-preserving workflows |
| `charteros/infrastructure` | SQLAlchemy persistence, outbox, graph storage, integrity and failure integration |
| `charteros/security` | verified principals, permissions, OIDC/JWT verification |
| `charteros/observability.py` | bounded-cardinality operational metrics |
| `apps/api` | FastAPI delivery, route security, governance/evidence/optimization surfaces |
| `apps/outbox_worker` | leased at-least-once delivery |
| `apps/graph_projection` | projection rebuild, verification and activation |
| `apps/recovery_verification` | restored-database verification and recovery manifest |
| `apps/resource_cleanup` | bounded cleanup of approved transient state |
| `tools` | assurance, release-manifest, benchmark, boot-smoke and load-evidence tooling |

## Authority map

### Canonical transactional authority

PostgreSQL owns authoritative business state:

- organizations/operators and fleet/catalog state;
- Missions, RFQs, Quotes, Tenders, approvals and Bookings;
- Contracts, disruptions and financial reconciliation;
- FX evidence and locks;
- aircraft capacity reservations;
- governance lifecycle/legal-hold evidence;
- transactional outbox and durable consumer receipts;
- database-enforced evidence-integrity streams.

### Derived / analytical authority

The following are intentionally narrower and cannot silently become command authority:

- Charter Graph projection;
- matching output;
- reposition optimizer output;
- pricing-intelligence datasets;
- reconstructed evidence packages;
- deterministic market simulation;
- Prometheus metrics and alerts;
- release/assurance manifests.

The Charter Graph is rebuildable from canonical outbox history. Metrics are observations, not
business state.

## Transaction and event reliability

Authoritative mutations and their outbox events commit in one PostgreSQL transaction. Delivery is
at-least-once with fenced leases, bounded retries, poison quarantine, stable event identity, and
durable database-consumer receipts.

Transaction failure handling distinguishes execution failure, commit failure, transient retryable
failure, and ambiguous commit. Ambiguous outcomes reconcile through durable idempotency/authority
state instead of blindly replaying a business action.

## Historical truth

CharterOS preserves event time and knowledge time separately. Bitemporal fleet state, no-hindsight
matching/pricing/evidence rules, immutable Quote/Tender revisions, and explicit supersession prevent
later knowledge from silently rewriting historical decisions.

## Security boundary

Production authentication is asymmetric OIDC/JWT verification with deny-by-default route capability
policy. Tenant headers are selectors, not identity.

General edge rate limiting belongs at trusted ingress. Application-owned expensive work uses shared
PostgreSQL budgets and hard request/query bounds.

`/metrics` is application-public so a monitoring plane can scrape it, but production ingress must
restrict access to the monitoring network/identity. Metric labels are bounded and exclude tenant and
object identifiers.

## Governance and evidence

Commercial data governance is policy-driven and dependency-aware. Closure, erasure, legal hold,
retention posture, and tenant export are separate concepts. Existing restrictive foreign keys and
append-only evidence rules are not relaxed to simplify deletion.

Evidence integrity is enforced in PostgreSQL and independently verifiable. Internal SHA-256 digests
are integrity evidence, not a claim of legal signature or external notarization.

## Recovery model

Backup/PITR creation is provider-specific. CharterOS owns the post-restore verification contract:

- Alembic revision;
- evidence-chain integrity;
- foreign-key orphan detection;
- capacity-overlap checks;
- outbox poison/lease state;
- future timestamp anomalies;
- current or rebuilt Charter Graph verification;
- observed RTO/RPO evidence when actual timestamps are supplied.

Recovery verification does not repair canonical history. Graph rebuild is explicit and never
auto-activates a new projection.

## Release / supply-chain model

The hardened container base is digest-pinned. Python dependencies are lockfile/hash/source checked.
External GitHub Actions used by release provenance are pinned to reviewed commit SHAs.

The CI quality job has read-only repository permissions. A separate provenance job receives
short-lived OIDC/attestation permissions and creates/verifies signed build provenance, CycloneDX SBOM
binding, and a signed canonical release manifest.

A signed CI artifact proves source/build/evidence binding. It does not prove deployment of that exact
digest or live production SLO attainment.

## Observability model

PR48 adds bounded-cardinality metrics for API, database pool/transactions, award, outbox, Charter
Graph, optimizer, and evidence paths. Persistent outbox/graph gauges are derived from PostgreSQL at
scrape time rather than stored as a competing truth source.

Candidate SLOs, Prometheus alert rules, operator runbooks, and a reproducible load/soak harness are
provided. Thresholds remain candidates until validated with target-environment evidence.

## Scaling posture

The modular-monolith boundary is deliberate. Scale pressure should first be handled through:

- bounded API/computation contracts;
- PostgreSQL indexing/transaction discipline;
- independent worker/process scaling where safe;
- measured pool/concurrency tuning;
- derived-state rebuildability;
- observed load/SLO evidence.

Service decomposition, Kubernetes, or new distributed authorities require measured need and an
explicit architecture decision; they are not assumed by the current design.
