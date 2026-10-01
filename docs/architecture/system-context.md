# CharterOS system context and container views

This document is the visual architecture atlas for CharterOS. It complements the durable decisions in
the [ADR index](../adr/README.md) and the narrative [architecture overview](README.md).

The diagrams use a C4-inspired hierarchy without claiming strict C4 notation. Their purpose is to make
system boundaries, authority, runtime responsibility, and derived-state relationships obvious to a
reviewer before they read implementation details.

## 1. System context

```mermaid
flowchart LR
    BUYER[Buyer organization]
    OP[Charter operator]
    ADMIN[CharterOS administrator]
    SVC[Trusted integration service]
    IDP[OIDC identity provider]
    MON[Monitoring / alerting plane]
    PG[(PostgreSQL)]
    CHARTEROS[[CharterOS]]

    BUYER -->|procurement, approvals, bookings| CHARTEROS
    OP -->|fleet, RFQs, quotes, operations| CHARTEROS
    ADMIN -->|governance, corrections, recovery operations| CHARTEROS
    SVC -->|machine-to-machine API calls| CHARTEROS
    IDP -->|signed identity assertions| CHARTEROS
    CHARTEROS -->|canonical transactional state| PG
    CHARTEROS -->|bounded metrics| MON
```

### Boundary statement

CharterOS is the application authority for charter procurement and execution workflows. PostgreSQL is
the canonical transactional system of record. Identity providers assert identity; they do not define
CharterOS business authorization. Monitoring observes the system; it does not own business state.

## 2. Runtime container view

```mermaid
flowchart TB
    subgraph Edge["Trusted ingress / deployment edge"]
      CLIENT[Buyer / Operator / Admin / Service]
    end

    subgraph Runtime["CharterOS runtime"]
      API[FastAPI API]
      AUTH[OIDC/JWT verification + route capability policy]
      APP[Application services]
      DOMAIN[Domain model]
      MATCH[Deterministic matching]
      OPT[Reposition optimizer]
      EVID[Evidence reconstruction]
      PI[Pricing intelligence]
      OW[Outbox worker]
      GP[Graph projection CLI/process]
      RV[Recovery verification CLI]
      RC[Resource cleanup CLI]
    end

    subgraph Canonical["Canonical persistence"]
      PG[(PostgreSQL)]
      OUTBOX[(Transactional outbox)]
      RECEIPTS[(Consumer receipts / projection cursors)]
    end

    subgraph Derived["Derived / non-command authority"]
      GRAPH[(Versioned Charter Graph)]
      DATASET[(Historical datasets)]
      METRICS[(Prometheus metrics)]
      RELEASE[(Release / provenance evidence)]
    end

    CLIENT --> API
    API --> AUTH --> APP
    APP --> DOMAIN
    APP --> PG
    APP --> OUTBOX

    PG --> MATCH
    PG --> OPT
    PG --> EVID
    PG --> PI

    OUTBOX --> OW
    OW --> RECEIPTS
    OW --> GRAPH
    GP --> GRAPH
    PI --> DATASET
    API --> METRICS
    PG --> METRICS

    RV --> PG
    RC --> PG

    DOMAIN -. invariants .-> APP
```

## 3. Code-to-runtime map

| Runtime concern | Primary code boundary | Authority |
| --- | --- | --- |
| HTTP delivery | `apps/api` | Delivery only |
| Authentication / authorization | `charteros/security`, API route policy | Identity verification + capability gate |
| Business orchestration | `charteros/application` | Use-case authority |
| Domain invariants | `charteros/domain` | Business-rule authority |
| Canonical persistence | `charteros/infrastructure` + PostgreSQL | Transactional truth |
| Matching | `charteros/matching` | Deterministic recommendation |
| Reposition optimization | `charteros/repositioning` | Deterministic recommendation |
| Event delivery | `charteros/outbox`, `apps/outbox_worker` | At-least-once delivery |
| Graph projection | `charteros/application/graph_projection.py`, infrastructure projection, `apps/graph_projection` | Rebuildable read model |
| Pricing intelligence | `charteros/pricing_intelligence`, `apps/pricing_intelligence` | Historical analytical view |
| Recovery assurance | `apps/recovery_verification` | Verification, never repair authority |
| Resource cleanup | `apps/resource_cleanup` | Bounded maintenance |
| Observability | `charteros/observability.py` + runtime instrumentation | Observation only |

## 4. Canonical versus derived state

```mermaid
flowchart LR
    PG[(PostgreSQL canonical state)]
    OUT[(Transactional outbox)]
    GRAPH[(Charter Graph)]
    MATCH[Matching result]
    OPT[Optimizer result]
    PI[Pricing dataset]
    EV[Evidence package]
    MET[Metrics]
    SIM[Market simulation]

    PG --> OUT --> GRAPH
    PG --> MATCH
    PG --> OPT
    PG --> PI
    PG --> EV
    PG --> MET
    SIM -. synthetic only .-> PI

    GRAPH -. no command authority .-> PG
    MATCH -. recommendation only .-> PG
    OPT -. recommendation only .-> PG
    PI -. analytical only .-> PG
    EV -. reconstruction only .-> PG
    MET -. observation only .-> PG
```

A reverse dotted arrow means “must not silently become command authority.” State may be rebuilt,
recomputed, or observed from canonical evidence, but mutations return through explicit application
services and database-enforced invariants.

## 5. Deployment posture

CharterOS is intentionally a modular monolith. The architecture keeps transactional correctness local
while allowing independent process scaling for API, outbox delivery, projection/rebuild work, recovery
verification, and maintenance tooling.

```mermaid
flowchart LR
    LB[Trusted ingress]
    API1[API instance]
    API2[API instance]
    W1[Outbox worker]
    W2[Outbox worker]
    OPS[Operator / CI jobs]
    PG[(PostgreSQL)]
    MON[Monitoring plane]

    LB --> API1
    LB --> API2
    API1 --> PG
    API2 --> PG
    W1 --> PG
    W2 --> PG
    OPS --> PG

    API1 --> MON
    API2 --> MON
    W1 --> MON
    W2 --> MON
```

Horizontal process count is not itself a correctness mechanism. Concurrency safety comes from
transaction boundaries, database constraints, fencing, idempotency, and explicit authority checks.

## 6. Related architecture documents

- [Architecture overview](README.md)
- [Critical flows](critical-flows.md)
- [Authority and trust boundaries](authority-and-trust-boundaries.md)
- [Architecture Decision Records](../adr/README.md)
- [Assurance documentation](../assurance/README.md)
