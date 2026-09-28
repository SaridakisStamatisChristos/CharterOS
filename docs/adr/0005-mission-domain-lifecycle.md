# ADR 0005 — Mission aggregate and lifecycle boundary

## Status

Accepted.

## Context

Roadmap PR5 introduces the first procurement aggregate. The mission must remain deterministic, auditable, exact for money, and compatible with the existing transactional outbox and idempotency boundaries without pulling RFQ or matching behavior forward.

## Decision

`Mission` is a framework-independent aggregate rooted by `MissionId`. PostgreSQL remains canonical. A mission records an active buyer organization, distinct canonical origin and destination airports, a UTC half-open departure window, positive passenger count, an optional positive `Money` budget, normalized special requirements, and explicit lifecycle status.

PR5 implements only creation, retrieval, and the guarded `DRAFT -> OPEN` transition. The enum records the handoff's future lifecycle vocabulary, but later transitions remain out of scope until their roadmap PRs.

Mission mutations use the existing idempotency records and advisory locks. Opening additionally locks the mission row and performs an optimistic version-checked update. `MISSION_CREATED` and `MISSION_OPENED` are committed atomically through the existing transactional outbox.

All mission foreign keys use `RESTRICT`. No graph, matching, RFQ, quote, payment, or microservice concerns are introduced here.

## Consequences

- retries cannot duplicate mission creation or opening side effects;
- concurrent open attempts cannot both advance the aggregate;
- exact budget semantics reuse the PR2 `Money` and `Currency` primitives;
- future sourcing/matching PRs receive a stable, auditable mission boundary;
- mission lifecycle evolution remains explicit rather than hidden in ad-hoc status writes.
