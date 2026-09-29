# ADR 0013 — Explicit Booking Workflow

## Status

Accepted for Roadmap PR13.

## Context

PR11 created exactly one `PENDING_CONTRACT` Booking at the atomic quote-award boundary. PR12 added a provider-neutral Contract with independent buyer/operator acceptance and deliberately did not advance Booking or Mission state when the Contract became fully accepted.

Roadmap PR13 owns the explicit Booking workflow. It must preserve PostgreSQL as canonical state, row-lock and optimistic-concurrency semantics, mutation idempotency, transactional outbox events, `RESTRICT` foreign keys, and the PR11/PR12 boundaries. It must not claim that payment settlement, flight execution, or reconciliation occurred through integrations that do not yet exist.

## Decision

### Booking state machine

PR13 implements one monotonic v1 happy path:

```text
PENDING_CONTRACT
  -> CONTRACTED
  -> PAYMENT_PENDING
  -> CONFIRMED
  -> PRE_OPERATION
  -> OPERATING
  -> COMPLETED
  -> RECONCILED
```

There is no generic `set-state` API. Each transition has a focused command endpoint and a domain method that validates the exact predecessor state.

### Contract guard

`PENDING_CONTRACT -> CONTRACTED` is legal only when the canonical Contract for the Booking exists and satisfies all PR12 acceptance invariants:

- `contract.booking_id == booking.id`;
- status is exactly `ACCEPTED`;
- buyer acceptance timestamp exists;
- operator acceptance timestamp exists;
- final `accepted_at` exists.

A missing, partial, or inconsistent Contract causes an explicit conflict and leaves Booking, Mission, idempotency, and outbox state unchanged.

### Mission coupling

Booking and Mission state are coupled only at milestones where the existing Mission vocabulary has a direct deterministic meaning:

```text
Booking CONTRACTED       -> Mission CONTRACTING
Booking PAYMENT_PENDING  -> Mission remains CONTRACTING
Booking CONFIRMED        -> Mission BOOKED
Booking PRE_OPERATION    -> Mission remains BOOKED
Booking OPERATING        -> Mission OPERATING
Booking COMPLETED        -> Mission COMPLETED
Booking RECONCILED       -> Mission remains COMPLETED
```

When both aggregates change, both row updates and both aggregate event streams are committed in the same database transaction.

### V1 command semantics and evidence boundary

PR13 models workflow acknowledgements, not integrations that belong to later roadmap PRs:

- `mark-payment-pending` means the Booking workflow has entered the payment-pending phase. It does **not** attest that funds were captured, authorized, settled, or received.
- `confirm` is an explicit CharterOS workflow confirmation. Until a canonical payment integration exists, it does **not** independently prove external payment settlement.
- `enter-pre-operation` acknowledges readiness to enter the pre-operation workflow phase. It does not prove permits, slots, handlers, manifests, or other future operational evidence.
- `start-operation` advances the workflow to operating. PR13 does not claim telemetry-derived proof that a flight departed.
- `complete` advances the workflow to completed. PR13 does not claim independent flight-completion telemetry or external operational evidence.
- `reconcile` acknowledges the v1 Booking workflow reconciliation step. It is not the PR24 reconciliation engine and does not fabricate invoices, settlement, variance, or ledger evidence.

Later payment, operational, disruption, and reconciliation capabilities may strengthen these guards by requiring canonical evidence. They must not reinterpret historical PR13 events as evidence those integrations existed.

### API commands

PR12 already owns `POST /v1/bookings/{booking_id}/contract` for Contract creation, so PR13 uses non-colliding, explicit command endpoints:

```text
POST /v1/bookings/{booking_id}/mark-contracted
POST /v1/bookings/{booking_id}/mark-payment-pending
POST /v1/bookings/{booking_id}/confirm
POST /v1/bookings/{booking_id}/enter-pre-operation
POST /v1/bookings/{booking_id}/start-operation
POST /v1/bookings/{booking_id}/complete
POST /v1/bookings/{booking_id}/reconcile
```

Every mutation requires `Idempotency-Key`. These v1 commands have no request body, so their canonical request hash is the deterministic empty object. Same key + same command returns the original response; a different idempotency key cannot repeat an already-consumed transition.

### Locking and concurrency

The PR13 transition lock order is:

1. Booking row (`FOR UPDATE`);
2. Contract row for `mark-contracted` only (`FOR UPDATE` by Booking ID);
3. Mission row (`FOR UPDATE`).

This makes the Booking row the serialization point for Booking workflow transitions. A simultaneous same-state transition therefore produces exactly one committed advancement. Contract acceptance locks only the Contract row; `mark-contracted` cannot bypass it because the Contract is read under a row lock and must already be fully accepted in the transaction-visible state.

Optimistic aggregate version checks remain in the repositories as a second line of defense against stale writes.

### Transition timestamps and audit reconstruction

`bookings.state_changed_at` records the UTC-aware timestamp of the latest Booking transition and is initialized to `created_at` for pre-PR13 rows. Each Booking transition emits one immutable event:

```text
BOOKING_CONTRACTED
BOOKING_PAYMENT_PENDING
BOOKING_CONFIRMED
BOOKING_PRE_OPERATION
BOOKING_OPERATING
BOOKING_COMPLETED
BOOKING_RECONCILED
```

Coupled Mission transitions emit:

```text
MISSION_CONTRACTING
MISSION_BOOKED
MISSION_OPERATING
MISSION_COMPLETED
```

The current rows, monotonic aggregate versions, Contract evidence, transactional outbox, and idempotency records together preserve a deterministic audit trail without introducing PR14 delivery infrastructure.

### Migration

Alembic `0011_booking_workflow`:

- adds and backfills non-null `bookings.state_changed_at`;
- replaces the PR11 single-state check with the explicit PR13 state vocabulary;
- adds `state_changed_at >= created_at` protection;
- preserves existing uniqueness, indexes, and `RESTRICT` foreign keys;
- refuses downgrade once a Booking has progressed beyond `PENDING_CONTRACT`.

## Consequences

The Booking lifecycle is explicit, race-safe, auditable, and idempotent while remaining honest about external evidence that CharterOS does not yet own. PR14 outbox delivery, payment processing, disruption handling, PR24 reconciliation, and PR26 FX remain out of scope.
