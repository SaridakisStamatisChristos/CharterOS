# Architecture Decision Records

ADRs record durable CharterOS technical decisions and their consequences.

Naming convention: `NNNN-short-title.md`.

## Foundation and domain

- [ADR 0001 — Modular Monolith + PostgreSQL Foundation](0001-modular-monolith-postgresql-foundation.md)
- [ADR 0002 — Shared Domain Primitives](0002-shared-domain-primitives.md)
- [ADR 0003 — Catalog Core Persistence](0003-catalog-core-persistence.md)
- [ADR 0004 — Bitemporal Aircraft Timeline](0004-bitemporal-aircraft-timeline.md)
- [ADR 0005 — Mission Domain Lifecycle](0005-mission-domain-lifecycle.md)
- [ADR 0006 — Deterministic Matching v1](0006-deterministic-matching-v1.md)
- [ADR 0007 — RFQ Engine](0007-rfq-engine.md)
- [ADR 0008 — Quote Engine](0008-quote-engine.md)
- [ADR 0009 — Quote Normalization](0009-quote-normalization.md)
- [ADR 0010 — Quote Comparison](0010-quote-comparison.md)
- [ADR 0011 — Quote Acceptance / Booking](0011-quote-acceptance-booking.md)
- [ADR 0012 — Contract Layer](0012-contract-layer.md)
- [ADR 0013 — Booking Workflow](0013-booking-workflow.md)

## Eventing, graph, optimization and market intelligence

- [ADR 0014 — Transactional Outbox](0014-transactional-outbox.md)
- [ADR 0015 — Charter Graph Projection](0015-charter-graph-projection.md)
- [ADR 0016 — Bounded Charter Graph Query Layer](0016-graph-query-layer.md)
- [ADR 0017 — Tender / Reverse Auction v1](0017-tender-reverse-auction.md)
- [ADR 0018 — Deterministic Repositioning / Deadhead Optimizer](0018-repositioning-deadhead-optimizer.md)
- [ADR 0019 — Deterministic Market Simulator](0019-market-simulator.md)
- [ADR 0020 — Pricing Dataset / Historical Intelligence](0020-pricing-historical-intelligence.md)

## Workflow, evidence and finance

- [ADR 0021 — Operator Portal APIs](0021-operator-portal-apis.md)
- [ADR 0022 — Buyer Procurement APIs](0022-buyer-procurement-apis.md)
- [ADR 0023 — Disruption Model](0023-disruption-model.md)
- [ADR 0024 — Financial Reconciliation](0024-financial-reconciliation.md)
- [ADR 0025 — Audit Evidence Layer](0025-audit-evidence-layer.md)
- [ADR 0026 — Auditable FX Policy](0026-auditable-fx-policy.md)

## Commercial and correctness hardening

ADRs 0027–0031 are the design record for the connected PR38–PR42 adversarial-hardening sequence.
For the composed authority/failure model across all five PRs, see the
[PR38–PR42 aircraft commitment hardening assurance map](../assurance/pr38-pr42-aircraft-commitment-hardening.md).

- [ADR 0027 — Aircraft Capacity Reservations](0027-aircraft-capacity-reservations.md)
- [ADR 0028 — Booking Termination / Capacity Release](0028-booking-termination-capacity-release.md)
- [ADR 0029 — Canonical Replacement-Aircraft Feasibility](0029-canonical-replacement-aircraft-feasibility.md)
- [ADR 0030 — Reposition Optimizer Input Completeness](0030-reposition-optimizer-input-completeness.md)
- [ADR 0031 — Award-Time Aircraft Feasibility Truth Gate](0031-award-time-aircraft-feasibility-truth-gate.md)
- [ADR 0032 — Transaction Failure and Ambiguous Commit](0032-transaction-failure-and-ambiguous-commit.md)
- [ADR 0033 — API Abuse and Resource Bounds](0033-api-abuse-resource-bounds.md)

## Enterprise assurance

- [ADR 0034 — Commercial Data Governance](0034-commercial-data-governance.md)
- [ADR 0035 — Deterministic Disaster-Recovery Assurance](0035-disaster-recovery-assurance.md)
- [ADR 0036 — Verifiable Release and Supply-Chain Provenance](0036-release-supply-chain-provenance.md)
- [ADR 0037 — Production Observability and Failure Evidence](0037-production-observability.md)

For operational procedures and evidence interpretation, see the
[documentation index](../README.md).
