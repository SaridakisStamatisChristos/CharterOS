# ADR 0002: Shared Domain Primitive Semantics

- Status: Accepted
- Date: 2026-09-28

## Context

CharterOS needs stable, framework-independent primitives before introducing persisted business
entities. Money, identifiers, temporal windows, event envelopes, and optimistic aggregate versions
must behave consistently across API, persistence, matching, and projection layers.

## Decision

- IDs are immutable UUID-backed value types. Domain-specific identifiers subclass `TypedId`,
  preserving runtime equality separation as well as static type separation.
- Money is immutable signed-int64 minor units plus a `Currency`. Binary floating point is not used.
  Cross-currency arithmetic and comparison fail explicitly.
- `Currency` validates the canonical three-uppercase-ASCII-letter ISO-4217 representation. Registry
  membership remains reference data so the domain kernel does not embed a stale currency table.
- `TimeRange` requires timezone-aware datetimes, canonicalizes them to UTC, and uses half-open
  `[start, end)` semantics. Touching windows therefore do not overlap.
- `DomainEvent` snapshots its JSON-compatible payload, requires aware event/record times, and emits
  canonical JSON with stable field/key ordering. Financial values must enter payloads as exact domain
  representations rather than binary floating-point currency amounts.
- `AggregateRoot` owns a non-negative optimistic version. Recording a domain event advances the
  aggregate version exactly once; pending events can be drained without changing that version.
- Version mismatches raise an explicit `OptimisticConcurrencyError`. Persistence-level compare-and-
  swap enforcement is deferred to the repository work in later PRs.

## Consequences

These primitives can be used by later aggregates without importing FastAPI, Pydantic, SQLAlchemy, or
infrastructure code. Their serialization and temporal semantics are deterministic and independently
testable.
