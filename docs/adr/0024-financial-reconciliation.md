# ADR 0024: Financial Reconciliation

- Status: Accepted
- Date: 2026-09-29
- Scope: Roadmap PR24 only

## Context

PR11 created the canonical Booking from one accepted Quote. PR9 established deterministic Quote
normalization. PR13 introduced a lightweight Booking lifecycle with a final `reconcile` command,
but ADR 0013 explicitly states that the PR13 command is only a workflow acknowledgement and is
not invoice, variance, settlement, or ledger evidence.

Roadmap PR24 must now make the financial reconciliation boundary real:

```text
booked commercial evidence
vs
final operator invoice
```

It must preserve the accepted Quote instead of rewriting it, expose explicit variance and surcharge
evidence, support buyer disputes and variance approval, compute a final payable deterministically,
and only then permit the Booking to become `RECONCILED`.

PR25 audit/evidence packaging and PR26 auditable FX remain later roadmap work.

## Decision

PR24 introduces an explicit canonical `FinancialReconciliation` aggregate in PostgreSQL. Exactly
one reconciliation may exist for a Booking.

A reconciliation snapshots the immutable booked commercial reference from the accepted Quote:

- Booking ID;
- accepted Quote ID and Quote revision;
- buyer and operator;
- currency;
- existing Quote-normalization version;
- normalized expected booked amount;
- normalized worst-case booked amount.

The accepted Quote itself is never changed by reconciliation.

The v1 variance baseline is the existing deterministic normalized expected total of the accepted
Quote. The normalized worst-case amount is retained beside it as supporting commercial evidence.
PR24 does not invent a new Quote-normalization formula.

## Booking authority

A reconciliation may be opened only for a canonical Booking in `COMPLETED` state whose Mission is
also `COMPLETED`.

The legacy PR13 HTTP command:

```text
POST /v1/bookings/{booking_id}/reconcile
```

now fails closed. PR13's historical `BOOKING_RECONCILED` semantics are not rewritten; instead,
the public transition is strengthened prospectively as ADR 0013 anticipated.

The only PR24 path to `COMPLETED -> RECONCILED` is successful financial reconciliation
completion. The reconciliation row and Booking transition are persisted in one transaction and both
event streams are committed through the existing transactional outbox.

The Booking aggregate remains the canonical Booking-state authority. PR24 does not create a second
Booking lifecycle.

## Operator invoice lineage

Operator invoices are immutable revisions.

Each revision records:

- invoice reference;
- exact currency;
- immutable ordered line items;
- exact invoice total;
- exact booked amount used for comparison;
- exact variance;
- surcharge reason where required;
- submission timestamp;
- supersession link when revised.

At most one invoice revision is current. Replacing an invoice supersedes the previous revision
rather than updating historical values.

Invoice lines use exact signed-int64 minor-unit `Money`. Their sum must equal the declared invoice
total exactly. Positive commercial lines must be positive; credits are explicit negative lines.

## Variance

PR24 defines:

```text
variance = final/current invoice total - booked amount
```

A positive variance requires a non-empty surcharge reason. A zero or negative variance requires no
buyer variance approval.

A positive variance is not silently accepted. The buyer must explicitly approve an amount no larger
than the current positive variance.

The final payable is deterministic:

```text
positive variance:
    final payable = booked amount + buyer-approved variance

zero/negative variance:
    final payable = current invoice total
```

The final payable may not exceed the current invoice total and must remain positive.

This supports negotiated partial approval without mutating either the original Quote or the
operator's historical invoice revision.

## Disputes

A buyer may dispute only the exact current invoice revision. Dispute evidence is immutable and
contains the disputed amount, reason, buyer identity, and timestamp.

If a disputed invoice is replaced by a later invoice revision, the previous dispute remains as
historical evidence but no longer governs the new current invoice.

A variance approval cannot silently erase a current dispute. When a dispute exists, the approval
must explicitly name the exact `resolves_dispute_id`. This makes dispute resolution causal and
prevents competing dispute/approval requests from overwriting each other's meaning.

## Completion

Financial reconciliation completion requires:

- the exact current invoice revision;
- no unresolved current dispute;
- exact current buyer variance approval when variance is positive;
- deterministic final payable;
- a Booking still canonically in `COMPLETED` state.

Successful completion records:

- final invoice revision;
- approved variance;
- final payable;
- completion timestamp.

The FinancialReconciliation aggregate is terminal after completion.

## Party isolation

PR24 uses the existing explicit application-context convention:

```text
X-Buyer-Id
X-Operator-Id
```

These headers are context selectors, not a claim of production authentication.

Buyer access is restricted to the buyer owning the canonical Mission for the Booking. Operator
access is restricted to the Booking's canonical operator. Cross-buyer and cross-operator access
fails closed.

## Currency and FX boundary

The operator invoice must use exactly the accepted Quote currency.

PR24 performs no FX conversion, uses no hidden rates, and does not create cross-currency settlement
comparisons. A currency mismatch fails explicitly.

PR26 remains the only roadmap PR that may introduce auditable FX.

## Events and outbox

Every material reconciliation mutation emits immutable domain events through the existing PR14
transactional outbox:

```text
FINANCIAL_RECONCILIATION_OPENED
OPERATOR_INVOICE_SUBMITTED
OPERATOR_INVOICE_SUPERSEDED
FINANCIAL_RECONCILIATION_DISPUTED
FINANCIAL_VARIANCE_APPROVED
FINANCIAL_RECONCILIATION_COMPLETED
```

Successful completion also emits the canonical Booking event:

```text
BOOKING_RECONCILED
```

PR24 preserves stable event IDs, aggregate versions, actors, correlation IDs, canonical JSON,
at-least-once delivery, retry/poison semantics, and durable consumer receipts. It does not claim
PR25 evidence packaging is complete.

## Idempotency and concurrency

All PR24 mutation endpoints use the established idempotency repository and PostgreSQL advisory
transaction locks.

The Booking row is the first row-level serialization point for reconciliation mutations, followed by
the FinancialReconciliation and current invoice rows. Optimistic aggregate versions remain a second
line of defense.

This prevents:

- duplicate reconciliation creation;
- duplicate invoice revisions on retry;
- contradictory current invoice authority;
- implicit dispute overwrite;
- duplicate variance approval;
- double terminal completion;
- two Booking `RECONCILED` transitions;
- partial reconciliation/Booking completion.

## Persistence

Migration `0017_financial_reconciliation` adds:

- `financial_reconciliations`;
- `operator_invoice_revisions`;
- `operator_invoice_lines`;
- `reconciliation_disputes`;
- `reconciliation_variance_approvals`.

All relational deletion behavior is `ON DELETE RESTRICT`. Partial unique indexes and constraints
protect current invoice authority, exact variance arithmetic, invoice-line signs, lifecycle state,
and final-completion evidence.

Downgrade refuses to destroy persisted financial reconciliation evidence.

## API surface

PR24 adds:

```text
POST /v1/bookings/{booking_id}/reconciliation
GET  /v1/bookings/{booking_id}/reconciliation
GET  /v1/reconciliations/{reconciliation_id}

POST /v1/reconciliations/{reconciliation_id}/invoices
GET  /v1/reconciliations/{reconciliation_id}/invoices

POST /v1/reconciliations/{reconciliation_id}/disputes
POST /v1/reconciliations/{reconciliation_id}/variance-approvals
POST /v1/reconciliations/{reconciliation_id}/complete
```

Business rules remain in typed application/domain services rather than the FastAPI layer.

## Consequences

PR24 replaces a workflow-only reconciliation acknowledgement with explicit financial evidence while
preserving all original commercial and Booking history.

It deliberately does not claim that CharterOS moved money, settled a payment rail, paid an invoice,
or posted accounting-ledger entries. `final_payable` is canonical reconciliation evidence, not
proof of external payment settlement.

## Out of scope

PR24 does not implement:

- PR25 audit/evidence packaging;
- PR26 FX;
- payment-rail settlement;
- general ledger/accounting integration;
- tax calculation engines;
- production authentication;
- production ML;
- mutation of accepted Quote history;
- cross-currency invoice reconciliation.
