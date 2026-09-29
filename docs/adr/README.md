# Architecture Decision Records

ADRs record durable technical decisions and their consequences.

Naming convention: `NNNN-short-title.md`.

Current decisions include the modular-monolith/PostgreSQL foundation, shared domain primitives,
bitemporal fleet history, procurement/booking workflows, the production transactional outbox,
and the versioned Charter Graph projection.
- [ADR 0016 — Bounded Charter Graph Query Layer](0016-graph-query-layer.md)

- [ADR 0017 — Tender / Reverse Auction v1](0017-tender-reverse-auction.md)

- [ADR 0018 — Deterministic Repositioning / Deadhead Optimizer](0018-repositioning-deadhead-optimizer.md)
