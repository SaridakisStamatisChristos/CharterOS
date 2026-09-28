# ADR 0009 — Deterministic quote normalization v1

## Status

Accepted for Roadmap PR9.

## Context

PR8 preserves operator-submitted commercial evidence exactly. PR9 must transform that heterogeneous
but structured evidence into a deterministic pricing view suitable for later buyer comparison.

The roadmap requires normalized:

- base price;
- known fees;
- conditional fees;
- exclusions;
- expected total;
- worst-case total;
- pricing confidence;
- caveats;
- unresolved components.

PR9 must remain deterministic and must not introduce ML, implicit FX, buyer ranking, quote
acceptance, or booking behavior.

## Decision

### Normalization is a derived read model

Normalization is computed from the canonical Quote aggregate and is not persisted as a second source
of truth.

The public endpoint is:

- GET /v1/quotes/{id}/normalization

The response includes normalization_version = v1 so later formula changes can be introduced
explicitly rather than silently changing historical interpretation.

No normalization domain event is emitted because the operation does not mutate canonical state.

### Explicit fee applicability

PR8 already persisted exact component amount, category, label, and optional descriptive condition.
A descriptive condition string is not reliable evidence that a fee is commercially contingent. For
example, "Known taxes at submission" contains text but is still a known fee.

PR9 therefore adds explicit component applicability:

- known
- conditional

Existing PR8 records migrate as known. API clients that omit applicability also receive the known
default, preserving PR8 behavior.

A conditional component must carry non-blank condition text. The database constrains the allowed
applicability values and requires a condition for persisted conditional rows.

This avoids keyword parsing and category heuristics.

### Normalized fee classification

Base charter remains a distinct base_price rather than a fee line.

A supplied repositioning cost is normalized as a known repositioning fee.

Each submitted price component maps one-to-one to a normalized fee using its explicit applicability.
Operator order is preserved inside the known and conditional partitions.

The existing categories remain canonical:

- fuel surcharge;
- airport fees;
- handling;
- parking;
- crew overnight;
- catering;
- deicing;
- permits;
- taxes;
- broker/service fee;
- other.

The other category is allowed because PR8 explicitly permits operator-defined labelled components,
but it produces a semantic caveat because its meaning is not standardized.

### Expected total

expected_total is calculated exactly as:

base price
+ repositioning cost, when supplied
+ every known price component

Conditional components are not assigned probabilities and are not partially weighted.

This is deliberately not an expected-value statistical estimate. In v1, "expected" means the exact
known-price baseline under the submitted applicability declarations.

### Worst-case total

worst_case_total is calculated exactly as:

expected_total
+ every explicitly priced conditional component

This represents the bounded maximum of all submitted priced components if every declared condition
triggers.

Unpriced exclusions are not invented or estimated. Therefore, when exclusions exist,
worst_case_total is not claimed to be a complete all-in upper bound. The response sets
totals_complete = false and emits an explicit caveat.

### Exclusions and unresolved components

Quote exclusions contain canonical text but no exact amount. PR9 returns them as:

- excluded_fees;
- unresolved_components with reason excluded_without_price.

They are omitted from both expected_total and worst_case_total because exact-money rules forbid
fabricating an amount.

### Exact money and currency

All normalization arithmetic uses the existing Money and Currency primitives.

PR9 performs:

- no binary floating-point money;
- no implicit FX conversion;
- no cross-currency addition;
- no saturation arithmetic.

Money overflow remains an explicit domain failure.

### Confidence

Pricing confidence is categorical and rule-based:

HIGH:
- no unpriced exclusions;
- no conditional fees;
- no operator-defined other components.

MEDIUM:
- no unpriced exclusions; and
- one or more conditional fees or operator-defined other components exist.

LOW:
- one or more unpriced exclusions remain unresolved.

The engine does not emit a synthetic percentage or learned probability. Caveats explain the
specific uncertainty source.

### Caveats

PR9 emits stable caveat codes plus human-readable messages:

- conditional_fees
- unpriced_exclusions
- operator_defined_other

Caveat ordering is deterministic.

### Multiple operator quote structures

Operators may submit different combinations of:

- base-only plus known fees;
- base plus repositioning;
- known structured components;
- explicitly conditional structured components;
- operator-defined other components;
- exclusions without prices.

The engine normalizes these structures using the same rules without operator-specific code or
string parsing.

### Revision behavior

Normalization operates on the requested persisted quote revision. It does not silently redirect a
historical quote ID to the current revision.

This preserves reproducibility of PR8 commercial history.

### Persistence and migration

Migration 0008_quote_normalization adds applicability to quote_price_components with a backward-
compatible known default and database constraints.

No normalized totals are persisted.

## API response

The normalization response exposes:

- normalization version;
- quote ID and revision number;
- currency;
- base price;
- known fees;
- conditional fees;
- excluded fees;
- expected total;
- worst-case total;
- totals completeness;
- confidence;
- caveats;
- unresolved components.

## Non-goals

PR9 does not implement:

- buyer quote comparison or ranking;
- mission-level comparison;
- hidden scoring;
- fee probability estimation;
- statistical expected value;
- FX conversion;
- price prediction;
- supplier intelligence or ML;
- quote acceptance/rejection;
- booking creation;
- contracts;
- payments;
- tender/reverse-auction behavior;
- UI;
- graph projection.
