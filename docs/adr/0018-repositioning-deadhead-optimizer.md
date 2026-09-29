# ADR 0018 — Deterministic Repositioning / Deadhead Optimizer

## Status

Accepted for Roadmap PR18.

## Context

PR16 exposes bounded structural empty-leg candidates from the active Charter Graph projection. Those
candidates identify gaps between sequential planned bookings for the same aircraft, but PR16
deliberately does not decide whether an empty move is physically feasible, economically attractive,
or worth replacing with a revenue mission.

PR18 owns that deterministic optimization layer. It must evaluate candidate future legs, preserve
aircraft continuity, enforce time windows, calculate reposition economics, and choose a globally
consistent assignment without machine learning.

The Charter Graph remains a non-canonical projection. Canonical fleet, mission, RFQ, Quote, airport,
and reference-profile state remains PostgreSQL/domain authority.

## Decision

### Structural source versus optimization authority

PR16 `empty_leg_candidates` is the structural source only. PR18 converts each graph candidate into a
neutral `StructuralEmptyLeg` and then revalidates the aircraft/operator relationship against
canonical fleet state.

A graph aircraft missing from canonical fleet state, or an operator ownership mismatch, is treated as
an inconsistency and fails closed.

### Candidate future legs

A future leg is economically eligible only when all of the following are true:

- the Mission is still in active sourcing/quoted state;
- the same Operator owns the RFQ;
- the same aircraft is the current submitted Quote aircraft;
- the Quote is current, submitted, already known at evaluation time, and not expired;
- the Mission departure window intersects the optimization horizon;
- the Quote is not part of an active sealed Tender.

Buyer budget is not treated as revenue.

Revenue comes from the existing deterministic Quote normalization policy. PR18 uses normalized
`expected_total` as quoted revenue and exposes the normalization confidence/completeness metadata.
It does not invent probability-weighted conditional fees.

### Sealed Tender isolation

Active sealed Tender quote evidence is excluded from PR18 opportunity discovery. The optimizer must
not become a side channel around PR17 sealed-bid visibility.

Closed or awarded Tenders are no longer in the sealed phase and may participate if their canonical
Mission/Quote lifecycle still otherwise qualifies.

### Feasibility and aircraft continuity

For every structural empty-leg window, PR18 evaluates:

1. direct baseline reposition from the previous Mission destination to the next Mission origin;
2. pre-reposition from that location to the candidate Mission origin;
3. the candidate revenue Mission;
4. post-reposition from candidate destination to the next Mission origin.

The existing matching reference profile is reused for:

- cruise speed;
- operating cost per hour;
- maximum reposition distance;
- turnaround buffer.

The existing matching distance/range/cost primitives are reused rather than introducing parallel
aviation math.

A candidate must preserve aircraft continuity before the next planned booking. PR18 schedules the
candidate at the earliest feasible instant inside its half-open Mission departure window after
pre-reposition and turnaround. It then requires enough time for the revenue leg, turnaround,
post-reposition, and final turnaround before the structural empty-leg window closes.

Aircraft capacity, route range, reposition range, operator verification, insurance, commercial
status, aircraft active status, and reference-profile presence all fail closed.

### Economics

All money remains exact signed-int64 minor-unit `Money`.

For one candidate insertion:

```text
baseline_reposition_cost
    = direct empty flight cost from previous destination to next origin

reposition_cost
    = pre-reposition cost + post-reposition cost

revenue_leg_operating_cost
    = operating cost of the candidate Mission route

gross_margin
    = quoted revenue - reposition_cost - revenue_leg_operating_cost

opportunity_cost
    = max(0, reposition_cost - baseline_reposition_cost)

continuity_adjusted_margin
    = quoted revenue - revenue_leg_operating_cost - opportunity_cost
```

The optimizer objective is continuity-adjusted margin. The baseline direct empty flight is treated as
an unavoidable continuity cost; only additional deadhead burden beyond that baseline is charged as
opportunity cost in the assignment objective.

Both gross margin and continuity-adjusted margin are exposed so the economic interpretation is
auditable.

Candidates with non-positive continuity-adjusted margin are not assignable.

### Min-cost flow / matching

PR18 v1 is a deterministic maximum-margin bipartite assignment implemented as min-cost flow over a
residual network.

Constraints:

- each structural empty-leg window receives at most one future Mission;
- each Mission is assigned at most once;
- residual reverse edges allow reassignment, so the result is globally optimal for the bounded
  single-insertion model rather than greedy;
- deterministic tie costs are strictly smaller than one minor currency unit of objective value.

The v1 optimizer deliberately supports one inserted revenue Mission per structural empty-leg window.
Multi-stop chaining is a later extension and is not silently approximated.

### Currency boundary

PR26 remains the future Auditable FX Policy.

PR18 performs no FX conversion and no cross-currency objective comparison. Feasible candidates are
partitioned by currency and optimized independently.

If more than one currency plan exists:

```text
global_plan_available = false
```

Currency plans are therefore scoped alternatives, not a globally ranked executable plan. No
cross-currency total margin is emitted.

### Read-only execution

PR18 is a recommendation/query capability. It does not mutate Mission, Quote, Booking, aircraft,
Tender, or graph state.

The HTTP endpoint runs in:

```text
REPEATABLE READ, READ ONLY
```

and exposes bounded limits for structural empty legs and quoted future opportunities.

### Determinism and evidence

The optimizer is deterministic for the same:

- active graph projection;
- canonical database state;
- evaluation timestamp;
- query horizon;
- limits.

The response exposes projection version, policy version, all selected booking/Mission/Quote IDs,
timing, distances, costs, revenue, margin, opportunity cost, and pricing-confidence evidence.

## Non-goals

PR18 does not implement:

- machine learning;
- stochastic demand or acceptance modeling;
- market simulation (PR19);
- historical analytics datasets (PR20);
- operator portal workflows (PR21);
- arbitrary graph query execution;
- a canonical Flight aggregate;
- multi-stop itinerary chaining inside one empty window;
- implicit FX or cross-currency global ranking;
- automatic booking or award mutation.

## Consequences

CharterOS now has an explainable optimization layer beyond static matching. The optimizer can turn
structural schedule gaps into auditable economic recommendations while preserving the existing
Mission/RFQ/Quote/Booking authority and Charter Graph non-canonical boundary.

The bounded single-insertion model is intentionally conservative. It creates a deterministic,
testable foundation for future time-expanded network-flow extensions without introducing ML or
prematurely changing canonical transaction semantics.
