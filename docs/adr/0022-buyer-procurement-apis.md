# ADR 0022: Buyer Procurement APIs and Pre-Award Approval Evidence

- Status: Accepted
- Date: 2026-09-29
- Roadmap: PR22 — Buyer Procurement APIs

## Context

CharterOS already has authoritative domain services for Missions, supplier matching, RFQs, Quotes, Tender/reverse-auction workflows, Quote comparison, Booking award, contracts, the transactional outbox, and the charter graph. PR22 needs a buyer-facing procurement workflow without duplicating any of those authorities.

The roadmap also separates **approval** from **award**. Before PR22, accepting a Quote was the first durable buyer decision and atomically created a Booking. There was no durable pre-award approval record.

## Decision

Expose `/v1/buyer-portal/*` as a composition layer. `X-Buyer-Id` identifies the buyer organization context for this API surface. It is an application context only; production authentication and credential verification remain deployment concerns.

The portal creates and opens Missions through `MissionService`; searches suppliers through the canonical deterministic `MatchingService`; issues RFQs through `RfqService`; compares current Quotes through `QuoteComparisonService`; records buyer approval as `ProcurementApproval`; awards only by delegating to `BookingService.accept_quote`; reads the canonical Booking for the Mission; and exposes a bounded metadata-only procurement event timeline.

## Approval semantics

`ProcurementApproval` is durable evidence that a buyer approved one immutable Quote revision. It is **not** a Quote status and is **not** Booking authority.

Only one active approval may exist per Mission. Approving a different current Quote revision supersedes the prior approval. Award consumes the active approval after canonical `BookingService` award succeeds.

An approval becomes stale if its Quote is revised, withdrawn, expired, rejected, or otherwise ceases to be the current submitted revision. A stale approval cannot be awarded; the buyer must approve the current revision.

Generic buyer approval/award does not bypass Tender authority. A Quote attached to a Tender invitation must remain in the Tender workflow.

## Buyer isolation

Every Mission-scoped portal operation verifies that the Mission belongs to the `X-Buyer-Id` organization. Cross-buyer resources fail closed with not-found semantics. Buyer context must refer to an active Organization.

## Supplier search and ranking

Supplier search reuses matching-v1 exactly. The portal performs no rescoring. It projects the first (best-ranked) feasible aircraft per Operator while preserving the canonical matching rank, score decomposition, feasibility reason codes, reference currency, and knowledge timestamp.

## Quote comparison and currency

Quote comparison reuses the canonical comparison policy. Ranking remains scoped to same-currency cohorts. The portal forwards `global_rank_available`; it does not introduce FX, synthetic exchange rates, or cross-currency ordering.

## Audit trail boundary

The PR22 audit endpoint is intentionally not the future PR25 Audit/Evidence layer. It returns only bounded deterministic event envelope metadata for aggregates belonging to the buyer's Mission procurement graph.

Raw `canonical_json` and event payloads are not exposed. Quote events belonging to an active sealed Tender are omitted from this generic audit surface, preserving sealed-bid confidentiality.

## Consistency and idempotency

Portal mutations use the existing transaction and idempotency infrastructure. Approval evidence is stored with optimistic versioning and emits transactional outbox events.

Read-heavy supplier, comparison, and audit endpoints use bounded deterministic reads; supplier, comparison, and audit snapshots use repeatable-read/read-only transactions where applicable.

## Consequences

- Buyer APIs are usable without creating a second procurement domain.
- Pre-award decisions are explicit and auditable.
- Award retains the existing single-winner Booking serialization boundary.
- Quote revision invalidates stale approval naturally.
- Tender confidentiality and authority remain intact.
- FX policy remains explicitly outside PR22.
- Full compliance/evidence packaging remains explicitly outside PR22.
