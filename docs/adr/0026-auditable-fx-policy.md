# ADR 0026: Auditable FX Policy and Short-Lived Buyer Price Locks

## Status

Accepted for Roadmap PR26, the final numbered CharterOS roadmap implementation PR.

## Context

Before PR26 CharterOS intentionally refused to compare prices across currencies. Canonical Quote
pricing is exact signed-int64 minor-unit Money, Quote normalization is deterministic and currency
preserving, and quote comparison ranks only same-currency cohorts. When more than one pricing
currency is present, global_rank_available is false.

PR26 must make cross-currency comparison possible without hidden rates, float arithmetic,
hindsight, mutable Quote pricing, or non-replayable live-provider behavior.

A buyer viewing an indicative conversion must not receive an indefinite FX reservation. However,
the buyer must also not click approve against a converted amount that changed after it was shown.
CharterOS therefore uses a very short executable lock window.

## Decision

### 1. Quote authority remains unchanged

The operator Quote remains canonical in its original currency. PR26 never rewrites Quote currency,
base price, price components, revision history, or Quote-normalization history.

FX is a separate evidence layer applied after deterministic Quote normalization.

### 2. Two FX concepts

FxRateObservation is immutable market/source evidence containing source and target currencies,
an exact decimal rate string, explicit source and target minor-unit exponents, source and source
version, provider/effective timestamp, CharterOS recorded-at timestamp, correction revision, and
immutable supersession lineage.

FxLock is a short-lived executable buyer commitment containing buyer and Mission identity, explicit
base currency and FX source, exact eligible Quote revision IDs, original normalized
expected/worst-case totals, exact FxRateObservation IDs where conversion is required, exact
converted expected/worst-case totals, global score/rank, policy versions, deterministic SHA-256
digest, lock/expiry timestamps, and terminal consumption evidence.

An FxLock does not replace the underlying rate or Quote.

### 3. Thirty-second lock

Policy:

    fx-lock-v1
    TTL = 30 seconds

A buyer-facing executable comparison is created by an idempotent mutation:

    POST /v1/buyer-portal/missions/{mission_id}/quotes/compare/fx-locks

The ordinary GET comparison remains read-only and does not reserve an FX value.

The lock is valid on the half-open interval [locked_at, expires_at). Approval at or after expires_at
fails explicitly. CharterOS does not silently reprice and continue the approval.

While a lock is active, newer FX observations or corrections affect new locks immediately but do
not alter the existing lock. The 30-second guarantee is therefore deliberate economic commitment,
not historical-display metadata.

### 4. Exact arithmetic

No float conversion is allowed.

Rates enter the domain as plain decimal strings and are represented with Decimal under a
high-precision local context. Conversion operates over original integer minor units:

    target_minor_exact =
        source_minor
        * exact_decimal_rate
        * 10^(target_minor_exponent - source_minor_exponent)

One centralized policy rounds to the target minor unit:

    half-even-target-minor-v1

Resulting Money must remain inside the existing signed-int64 domain. Source and target minor-unit
exponents are explicit rate evidence. No default exponent is silently invented by the conversion
engine.

### 5. Explicit direction; no reciprocal or triangulation inference

Rates are directional. An observation for USD -> EUR does not implicitly authorize EUR -> USD.

PR26 also does not triangulate through a third currency. If the required explicit pair is absent,
cross-currency lock creation fails.

### 6. Bitemporal / no-hindsight resolver

Each rate has fx_timestamp and recorded_at.

For decision cutoff T, a rate is eligible only when:

    fx_timestamp <= T
    recorded_at  <= T

Correction revisions are immutable new rows. A later-recorded correction is invisible to historical
decisions before that correction was known.

Among eligible observations for one source/pair, CharterOS selects the latest effective
fx_timestamp and latest non-superseded revision known at the cutoff.

### 7. Corrections are immediate and append-only

A provider correction is handled on the fly through:

    POST /v1/fx/rates/{rate_id}/corrections

It creates a new immutable revision referencing supersedes_rate_id. It never updates the prior rate
in place. The corrected value is immediately available to newly created locks.

The supersession edge is unique. Row locking plus database uniqueness prevent concurrent correction
branches from becoming canonical successors of the same observation.

### 8. Idempotency

External FX ingestion and correction use existing CharterOS idempotency-record/advisory-lock
semantics.

Buyer FX lock creation uses the same idempotency mechanism. A retry with the same Idempotency-Key
returns the same lock instead of extending or refreshing its window.

### 9. Global ranking

Existing same-currency score/rank remains visible and unchanged.

Cross-currency global ranking is available only after every decision-eligible quote has explicit
conversion into the requested base currency. Operationally infeasible or commercially invalid
quotes do not need fabricated FX evidence merely to remain ineligible.

For an eligible quote already in the base currency, PR26 uses explicit identity conversion with
rate 1 and source identity. No external rate row is fabricated.

The existing deterministic comparison scoring algorithm is reused over converted base-currency
Money values. Global rank is stored inside the lock so buyer-visible ordering is replayable.

### 10. Approval is the commitment boundary

The buyer sends the selected lock ID with approval.

Inside one transaction CharterOS verifies buyer/Mission ownership, Quote/Mission lineage, current
Quote revision and validity, current decision eligibility, FxLock ownership, active status,
30-second window, exact Quote revision, and equality between locked original totals and immutable
Quote normalization.

Then CharterOS atomically creates or supersedes ProcurementApproval, consumes the FxLock, records
the exact approval identity on the lock, emits both aggregate event streams through the PR14
transactional outbox, and persists PR25 decision evidence containing the exact committed FX
context.

A lock can back only one approval. After successful approval, later market changes do not rewrite
that committed decision evidence.

### 11. Award and payment boundary

PR22/BookingService remains canonical award authority. PR26 creates no second award path.

PR26 does not claim that the recorded base-currency amount represents external payment settlement,
treasury execution, hedging, or provider fill. Repository evidence proves what CharterOS committed
internally, not what an external rail executed.

### 12. Evidence reconstruction

PR25 remains the reconstruction authority.

For consumed buyer FX locks, reconstruction includes the fx_lock aggregate/event lineage,
referenced fx_rate aggregate/event lineage, exact lock conversion rows, source/version/timestamps,
original and converted minor-unit totals, global rank/score, policy identifiers, lock digest, and
consumption identity.

Evidence verification recomputes converted values from immutable rate evidence and recomputes the
lock digest. Missing rates, conflicting rows, altered converted amounts, event gaps, or broken
snapshot references fail explicitly.

Operator evidence does not receive buyer FX locks or buyer comparison evidence.

### 13. API shape

Rate ingestion:

    POST /v1/fx/rates
    POST /v1/fx/rates/{rate_id}/corrections
    GET  /v1/fx/rates/{rate_id}

Buyer comparison:

    GET  /v1/buyer-portal/missions/{mission_id}/quotes/compare
    POST /v1/buyer-portal/missions/{mission_id}/quotes/compare/fx-locks

Approval optionally binds fx_lock_id.

An approval without an FxLock remains an approval of the original operator Quote currency and does
not claim a CharterOS base-currency guarantee.

### 14. Persistence

Migration:

    0019_auditable_fx

Tables:

    fx_rates
    fx_locks
    fx_lock_conversions

All foreign keys use RESTRICT semantics. Existing canonical history is not cascaded away.

Migration downgrade refuses to delete persisted immutable FX evidence.

## Consequences

CharterOS gains auditable cross-currency comparison while preserving exact Money, immutable Quotes,
same-currency ranking, PR14 event delivery, PR22 approval/Booking authority, and PR25 evidence
reconstruction.

The system can ingest provider corrections continuously without breaking a buyer's active 30-second
commitment or rewriting a historical decision.

## Explicit non-goals

PR26 does not implement implicit FX, floating-point conversion, reciprocal-rate inference,
triangulation, live-provider-only historical reconstruction, indefinite rate reservation, silent
repricing on lock expiry, Quote mutation, payment settlement, treasury hedging, external-provider
fill certification, production authentication, or production ML.
