# ADR 0027: PostgreSQL-Enforced Aircraft Capacity Reservations

## Status

Accepted for adversarial hardening PR38.

## Context

CharterOS already serializes award by Mission, which guarantees one Booking winner for competing
Quotes on the same Mission. That boundary does not prevent two independent Missions from committing
the same aircraft to overlapping operational time.

Application pre-checks are not sufficient because two concurrent transactions can both observe an
apparently free tail before either commits. PR38 therefore needs a resource-level concurrency
boundary below the application layer without changing the existing Mission, Quote, Tender, FX,
authorization, evidence, clock, or transactional-outbox invariants.

CharterOS does not yet model a precise scheduled flight-leg arrival as first-class observed truth.
The reservation interval must therefore be deterministic and conservative without inventing an
arrival observation.

## Decision

### Separate resource aggregate

PR38 introduces `AircraftCapacityReservation`. It answers one question only:

> Does this aircraft currently block this interval from another CharterOS commitment?

It is not a shadow Booking lifecycle. Its PR38 state is deliberately limited to `reserved` and
`released`; PR39 owns the commercial cancellation/expiry paths that can perform the release.

Each reservation records the Booking, Mission, operator, aircraft, half-open UTC interval, policy
version, matching-reference-profile identity/time, route distance, route duration, and turnaround
buffer used to derive the interval.

### Capacity policy v1

The policy version is `aircraft-capacity-v1`.

For the quoted aircraft, CharterOS resolves the canonical matching reference profile known at the
award decision time. It then reuses the same deterministic route distance and integer-ceiling
`flight_minutes` primitives used by matching.

The occupied interval is:

```text
[ mission.departure_window.start,
  mission.departure_window.end
    + route_flight_duration
    + matching_reference_profile.turnaround_buffer )
```

This is intentionally conservative. It covers the entire possible departure window and adds
deterministic route flight time plus turnaround after the latest allowed departure.

No floating-point temporal arithmetic or wall-clock read is introduced. If the aircraft, Mission
airports, or matching reference profile required to reproduce the interval is unavailable, award
fails closed.

### PostgreSQL is the final overlap authority

The table stores a generated `tstzrange(starts_at, ends_at, '[)')` and uses a GiST exclusion
constraint over:

```text
aircraft_id WITH =
occupied_range WITH &&
WHERE status = 'reserved'
```

`btree_gist` supplies GiST equality support for the UUID aircraft key. Half-open intervals allow a
new reservation to start exactly when an earlier reservation ends.

This exclusion constraint is the concurrency boundary. There is no race-prone
"SELECT free, then INSERT" correctness dependency.

### Award transaction order

After existing Mission/Quote/RFQ/Tender validity checks, Booking award:

1. derives the versioned capacity plan;
2. creates the Booking aggregate;
3. creates the reservation aggregate;
4. flushes the Booking row to satisfy the reservation foreign key;
5. inserts the reservation, allowing PostgreSQL to arbitrate any overlapping race;
6. only after capacity succeeds, mutates Quote and Mission award state;
7. writes Booking, reservation, Quote, Mission, and outbox events in the same transaction.

If the exclusion constraint rejects the reservation, the transaction rolls back. No Booking,
reservation, Quote acceptance, Mission selection, idempotency response, evidence-chain append, or
award event survives.

### Evidence integrity

Reservation identity and policy inputs are immutable. A database trigger permits only the future
`reserved -> released` resource transition and rejects deletion. Reservation insertion is also
captured by the existing PR28 evidence-integrity chain, excluding only the lifecycle fields that
PR39 may legitimately advance.

The migration reapplies the conventional `charteros_runtime` privilege policy when that role
exists. Deployments using a differently named runtime role must continue to run the existing
PR28 privilege-application function after migrations.

## Consequences

Two different Missions can no longer produce internally valid overlapping commitments for the same
tail. Concurrent overlap is decided by PostgreSQL, while non-overlapping commitments for the same
aircraft remain valid.

The interval is reproducible from persisted evidence and policy version rather than a guessed
arrival timestamp. The design intentionally does not add temporary holds, cancellation, expiry,
payment deadlines, crew scheduling, permit management, or disruption replacement semantics; those
remain separate roadmap work.
