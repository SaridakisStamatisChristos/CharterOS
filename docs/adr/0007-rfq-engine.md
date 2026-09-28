# ADR 0007 — RFQ procurement lifecycle

## Status

Accepted for Roadmap PR7.

## Context

PR7 introduces the first supplier-facing procurement aggregate after deterministic matching. The roadmap requires RFQ creation, a target operator, response deadline, acknowledgement, decline, expiry, and immutable domain events. The canonical RFQ state vocabulary is:

`CREATED -> SENT -> ACKNOWLEDGED -> QUOTED`

with terminal alternatives `DECLINED`, `EXPIRED`, and `WITHDRAWN`.

PR8 owns quote creation, so PR7 must not implement the `QUOTED` transition.

## Decision

### Creation and sending

The required API exposes `POST /v1/missions/{id}/rfqs` but no separate send endpoint. PR7 therefore creates and sends an RFQ atomically in one transaction. The aggregate still performs two explicit transitions and emits both `RFQ_CREATED` and `RFQ_SENT`.

The first RFQ for an `OPEN` mission advances the mission to `SOURCING` and emits `MISSION_SOURCING`. Additional RFQs may be created while the mission is already `SOURCING`.

### Supplier eligibility

The target operator must exist and be:

- verified;
- currently insured;
- commercially active.

The database permits only one RFQ for a given mission/operator pair in v1. Retender/revision behavior belongs to later tender work.

### Deadline semantics

All RFQ timestamps are timezone-aware UTC. A response deadline must:

- be strictly after issuance;
- be strictly before the mission departure time.

Acknowledgement or decline at or after the response deadline is rejected.

Expiry is a persisted state transition, not a computed presentation flag. PR7 adds `POST /v1/rfqs/{id}/expire` as an explicit idempotent system/admin command because the roadmap requires expiry but does not provide a scheduler in this phase. Expiry is valid only at or after the response deadline.

### Concurrency and idempotency

All RFQ mutations use the existing idempotency store. State transitions lock the RFQ row and use optimistic aggregate versions. Concurrent acknowledge/decline/expire commands therefore serialize and only a transition valid for the resulting canonical state can commit.

Foreign keys remain `RESTRICT`.

### Events

PR7 emits:

- `MISSION_SOURCING`
- `RFQ_CREATED`
- `RFQ_SENT`
- `RFQ_ACKNOWLEDGED`
- `RFQ_DECLINED`
- `RFQ_EXPIRED`

All are written through the existing transactional outbox in the same database transaction as state changes.

## Non-goals

PR7 does not implement quotes, pricing, quote normalization, tender revisions, withdrawal, scheduled background expiry, notifications, or graph projection.
