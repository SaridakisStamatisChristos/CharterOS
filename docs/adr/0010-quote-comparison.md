# ADR 0010 — Explainable quote comparison and buyer decision support v1

## Status

Accepted for Roadmap PR10.

## Context

PR8 preserves canonical operator-submitted quotes and revision history. PR9 deterministically
normalizes their commercial structure. PR10 must provide a buyer-facing mission comparison across
the current authoritative quotes while preserving exact money, operational feasibility, and
explainability.

The roadmap requires comparison of:

- expected total;
- worst-case total;
- aircraft suitability;
- repositioning;
- terms;
- cancellation policy;
- payment terms;
- operational risk.

It also requires no hidden ranking and an exposed score decomposition.

PR10 is decision support only. PR11 owns quote acceptance and booking creation. The comparator
never auto-selects, awards, or marks a quote as the buyer's winner.

## Decision

### Read-only mission comparison

PR10 exposes:

- GET /v1/missions/{id}/quotes/compare

The endpoint is evaluated in a PostgreSQL repeatable-read, read-only transaction so RFQs, current
quotes, fleet evidence, and reference profiles are observed consistently within one response.

PR10 does not mutate Mission, RFQ, Quote, or Booking state and emits no domain event.

### Quote population

The comparison uses one current authoritative PR8 quote per RFQ. Superseded, withdrawn, and
explicitly expired quotes are not current and therefore are not part of the active decision set.

A quote that is still persisted as submitted/current but whose valid_until has already passed is
returned for transparency but marked commercially invalid and decision-ineligible. PR10 does not
silently perform the PR8 expiry state transition.

### Pricing source

Every quote is normalized by the exact PR9 v1 normalization policy.

The buyer response exposes:

- base price;
- known fees;
- conditional fees;
- exclusions;
- expected total;
- worst-case total;
- totals completeness;
- pricing confidence;
- caveats;
- unresolved components.

PR10 performs no additional price estimation.

### No implicit FX

Quotes are never converted between currencies.

Ranking is scoped to a quote's currency cohort. A EUR quote and USD quote may be displayed in the
same response but they never receive a shared price ranking.

The response declares:

- pricing_currencies;
- ranking_scope = currency;
- global_rank_available.

global_rank_available is false whenever more than one pricing currency is present.

### Operational source and aircraft suitability

PR10 reuses the PR6 deterministic matching policy rather than inventing a second feasibility model.

Only the aircraft referenced by active quotes are loaded from the canonical PostgreSQL fleet,
position, availability, operator, and matching-reference state.

The same PR6 rules evaluate:

- aircraft active state;
- operator verification, insurance, and commercial status;
- passenger capacity;
- range with reserve;
- mission-window availability;
- known position;
- matching reference profile;
- reposition distance;
- reposition timing;
- reference currency support.

The response exposes PR6 reason/rejection codes and the evidence timestamps used for position,
availability, and reference profile interpretation.

Aircraft infeasibility is a hard buyer-decision eligibility gate.

### Commercial validity gate

A quote is decision-eligible only when both are true:

1. the quote is current, submitted, already submitted as of evaluation, and before valid_until;
2. the quoted aircraft is operationally feasible under PR6.

An ineligible quote remains visible but receives no aggregate score or currency rank.

This prevents a low commercial price from algorithmically outranking an impossible or stale quote.

### Repositioning

Two distinct concepts remain visible:

- submitted repositioning cost from the operator quote;
- operational repositioning distance calculated by PR6 from canonical fleet position evidence.

The pricing engine includes submitted repositioning cost according to PR9 rules. The comparison
score uses operational reposition distance as a separate decision dimension.

### Operational risk

PR10 uses the existing PR6 schedule_risk_basis_points as its operational-risk metric.

It also exposes timing_buffer_minutes and the underlying feasibility reason codes. No new learned
risk model is introduced.

### Free-text terms

Inclusions, exclusions, cancellation terms, and payment terms are returned verbatim from the
canonical quote.

PR10 deliberately does not assign positive/negative numeric sentiment to cancellation or payment
language. Presence of text is not evidence that the terms are economically favorable.

The API explicitly returns free_text_terms_scored = false.

Later structured contract/policy modeling may introduce objective term dimensions without changing
this v1 interpretation silently.

### Score decomposition

Eligible quotes are scored only against other eligible quotes in the same currency cohort.

The v1 maximum is 10,000 basis points:

- expected total: 2,500 points;
- worst-case total: 2,500 points;
- reposition distance: 2,000 points;
- operational schedule risk: 2,000 points;
- PR9 pricing confidence: 1,000 points.

For expected total, worst-case total, reposition distance, and schedule risk, lower is better.
Each dimension uses deterministic cohort min-max scaling. If every eligible value in a dimension is
identical, every eligible quote receives the full weight for that dimension.

Pricing-confidence points are absolute:

- high: 1,000;
- medium: 500;
- low: 0.

Free-text terms are not part of the score.

The API exposes every component, method name, weight, currency scope, cohort size, and total.

### Stable ranking

Eligible quotes are ordered within currency by:

1. descending total score;
2. lower expected total;
3. lower worst-case total;
4. lower reposition distance;
5. stable quote UUID.

This provides deterministic replay for identical canonical inputs.

No single global rank is asserted across currencies.

### Temporal semantics

The comparison response records evaluated_at.

Operational snapshot lookup uses evaluated_at as the knowledge cutoff and caps position event time
at mission departure start, preserving the PR6 no-hindsight boundary for fleet evidence.

PR10 v1 is an active-current comparison API, not a historical quote-revision reconstruction API.
It intentionally compares the current authoritative PR8 revision for each RFQ.

### Persistence

PR10 adds no canonical persistence table and no Alembic migration.

Comparison scores are derived outputs. PostgreSQL remains canonical for mission, RFQ, quote, fleet,
and reference data.

## Non-goals

PR10 does not implement:

- quote acceptance or rejection;
- booking creation;
- Mission SELECTED/BOOKED transitions;
- loser-closing mutations;
- contracts;
- payments;
- FX conversion;
- sentiment/NLP scoring of terms;
- ML pricing or risk models;
- reverse auction/tender rounds;
- graph projection;
- UI.

These remain later roadmap scope, especially PR11 for transactional acceptance and booking creation.
