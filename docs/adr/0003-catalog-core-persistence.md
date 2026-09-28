# ADR 0003: Catalog Core Persistence and Mutation Semantics

- Status: Accepted
- Date: 2026-09-28

## Context

PR3 introduces the first persisted CharterOS business entities: organizations, operators, airports,
aircraft types, and aircraft. These records become canonical inputs to later fleet-state, mission,
matching, RFQ, quote, and booking workflows. The API must therefore establish durable uniqueness,
referential-integrity, event, and retry semantics before higher-level workflows depend on them.

## Decision

- PostgreSQL remains the canonical system of record.
- Domain entities remain independent of SQLAlchemy and FastAPI.
- Organization legal-name uniqueness is case-insensitive within a country through a canonical
  normalized key.
- An organization can own at most one operator profile; AOC references are canonicalized and unique.
- ICAO codes are unique; non-null IATA codes are unique.
- Aircraft registrations are canonicalized to uppercase and globally unique.
- Aircraft type identity is canonical manufacturer + model. `POST /v1/aircraft` may create the
  supporting aircraft-type record on first use; later attempts with the same make/model must match
  the existing canonical reference data rather than silently overwrite it.
- Aircraft must reference an existing operator, aircraft type, and home-base airport.
- Foreign keys use `RESTRICT`; entity deletion is not made implicit through cascading relationships.
- Creation events are written to a transactional `outbox_events` table in the same database
  transaction as the canonical entity mutation.
- Every PR3 POST endpoint requires `Idempotency-Key`. The request body is hashed canonically and the
  response is stored transactionally. The same key + same body returns the original response; the
  same key + different body returns a conflict.
- PostgreSQL advisory transaction locks serialize requests sharing the same endpoint/key pair,
  preventing concurrent retries from duplicating side effects.
- Syntactic validation exists at the API boundary and domain invariants are revalidated in the
  framework-independent domain model. Database uniqueness, foreign keys, and check constraints form
  a final integrity boundary.

## Consequences

Later PRs can safely build temporal aircraft state and procurement workflows on stable catalog
identities. Event publication can be added without dual-writing canonical state and a message broker.
Retries have explicit semantics from the first externally callable mutation API.

PR3 deliberately does not implement aircraft positions, availability timelines, historical
reconstruction, matching, missions, RFQs, quotes, or bookings.
