# ADR 0028: Deterministic Booking Termination and Capacity Release

## Status

Accepted for adversarial hardening PR39.

## Context

PR38 makes PostgreSQL the final authority for overlapping aircraft commitments by introducing a
separate `AircraftCapacityReservation` aggregate and a GiST exclusion constraint over active
reservations. Without a deterministic release path, cancelled or commercially expired bookings
would leave permanent ghost capacity and make the PR38 invariant operationally unusable.

The release path must not turn the reservation into a second Booking lifecycle. It must preserve
Quote immutability, Tender/FX semantics, no-hindsight rules, evidence integrity, transactional
outbox behavior, and the existing pre-operation Booking workflow.

## Decision

### Explicit terminal reasons

PR39 defines five canonical reasons:

- `contract_unsigned` — system expiry from `pending_contract`
- `deposit_timeout` — system expiry from `payment_pending`
- `commercial_expiry` — system expiry from `pending_contract` or `payment_pending`
- `buyer_cancel` — buyer cancellation from `pending_contract`, `contracted`, or
  `payment_pending`
- `operator_release` — operator cancellation from the same three pre-confirmation states

The semantic source is derived from the reason and is persisted/evented as `buyer`, `operator`,
or `system`; clients cannot forge a mismatched reason/source pair.

PR39 deliberately does not define cancellation after `confirmed`. Confirmed, pre-operation,
operating, completed, and reconciled bookings remain outside this release path.

### Booking and Mission terminal state

Booking gains terminal states `cancelled` and `expired`, plus persisted
`termination_reason` and `termination_source`. Database checks require terminal evidence exactly
when the Booking is terminal.

The corresponding Mission moves from `selected` or `contracting` to `cancelled` or
`expired`. Terminal events carry prior state/status, reason, source, and transition timestamp.

### Capacity release

The PR38 reservation remains a resource aggregate with only:

```text
reserved -> released
```

Release changes only `version`, `status`, `released_at`, and `release_reason`. Identity,
aircraft, Booking, Mission, operator, interval, policy version, reference-profile evidence, route
distance, duration, and turnaround inputs remain immutable under the PR38 database guard.

A released reservation no longer participates in the partial GiST exclusion constraint, allowing
the same aircraft interval to be committed again.

### Atomic command boundary and lock order

The application locks:

1. Booking;
2. its coupled Mission;
3. its capacity reservation.

It then validates reason/state compatibility and reservation identity before mutating anything.
Booking termination, Mission termination, reservation release, idempotency response, and all three
domain/outbox events commit in one SQLAlchemy/PostgreSQL transaction.

A failure rolls the entire command back. Competing terminal commands serialize on the Booking row;
only one can create terminal/release evidence.

### API

Two idempotent commands preserve explicit intent:

```text
POST /v1/bookings/{booking_id}/cancel
POST /v1/bookings/{booking_id}/expire
```

Cancellation accepts only `buyer_cancel|operator_release`. Expiry accepts only
`contract_unsigned|deposit_timeout|commercial_expiry`. The request body participates in the
idempotency hash, so reusing a key with a different reason conflicts.

### Projection and evidence

Booking and Mission terminal events are explicitly supported by Charter Graph v1 so the new events
cannot poison projection replay. The reservation aggregate remains non-projected.

The transactional outbox remains the immutable transition evidence boundary. PR38's reservation
identity/policy evidence remains unchanged; PR39 uses the release fields that PR38 explicitly left
mutable for this purpose.

## Consequences

PR38 no longer creates permanent ghost capacity. Commercially dead pre-confirmation bookings free
their aircraft atomically and reproducibly, while concurrent release commands cannot double-release
or fork terminal evidence.

PR39 does not introduce payment schedulers, contract deadline workers, automatic wall-clock jobs,
refund policy, post-confirmation cancellation, disruption replacement, crew/permit scheduling, or
optimizer changes.
