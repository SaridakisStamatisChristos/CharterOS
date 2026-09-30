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

For every structural empty-leg window, PR18 first resolves both bounding Booking lineages from
the active graph and verifies that aircraft, operator, Mission, airport, and window evidence agrees
with the structural candidate. The raw PR16 window starts at the end of the previous Mission's
*departure* window, which is not yet aircraft availability at the destination. PR18 therefore
conservatively computes the previous booked Mission's great-circle flight duration from its origin
to the structural `from_airport`, adds turnaround, and uses that derived timestamp as
`aircraft_available_at`.

PR18 then evaluates:

1. the previous booked revenue leg needed to establish actual destination availability;
2. direct baseline reposition from the previous Mission destination to the next Mission origin;
3. pre-reposition from that location to the candidate Mission origin;
4. the candidate revenue Mission;
5. post-reposition from candidate destination to the next Mission origin.

The existing matching reference profile is reused for:

- cruise speed;
- operating cost per hour;
- maximum reposition distance;
- turnaround buffer.

The existing matching distance/range/cost primitives are reused rather than introducing parallel
aviation math.

A candidate must preserve aircraft continuity before the next planned booking. PR18 schedules the
candidate at the earliest feasible instant inside its half-open Mission departure window after the
derived `aircraft_available_at`, pre-reposition, and turnaround. It then requires enough time for
the revenue leg, turnaround, post-reposition, and final turnaround before the structural empty-leg
window closes. Using the latest previous departure bound makes v1 conservative rather than assuming
an aircraft is already at the previous Mission destination when its departure window ends.

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
      when direct baseline reposition is feasible
    = reposition_cost
      when the direct baseline is infeasible

continuity_adjusted_margin
    = quoted revenue - revenue_leg_operating_cost - opportunity_cost
```

The optimizer objective is continuity-adjusted margin. When the baseline direct empty flight is
feasible, it is treated as an unavoidable continuity cost and only additional deadhead burden is
charged as opportunity cost. A direct baseline that violates deadhead/range policy does **not**
discard the structural gap: a revenue stop can split the movement into feasible segments. In that
case the full pre/post reposition cost is charged as opportunity cost, which is conservative.

Both direct-baseline feasibility, gross margin, and continuity-adjusted margin are exposed so the
economic interpretation is auditable.

Candidates with non-positive continuity-adjusted margin are not assignable.

### Exact matching and tie semantics

The optimizer is an exact maximum-margin bipartite assignment. PR18 originally used residual
min-cost flow; PR32 replaced the hot path with an exact shortest-augmenting-path Hungarian
implementation while preserving the same scalar economics.

Constraints:

- each structural empty-leg window receives at most one future Mission;
- each Mission is assigned at most once;
- reassignment remains globally optimal for the bounded single-insertion model rather than greedy;
- one minor unit of margin dominates the aggregate candidate-index tie penalty.

The scalar objective alone does not totally order every feasible plan: different assignment sets can
have identical margin and identical aggregate candidate-index penalty. `reposition-v2` therefore
defines a third, canonical tie-break independent of solver traversal:

- sort structural empty-leg keys using the solver's existing left-key order;
- sort Mission UUIDs using the solver's existing right-key order;
- represent a plan as the Mission-rank vector for those sorted left keys, with unmatched after every
  real Mission; and
- choose the lexicographically smallest vector after the first two objectives tie.

The mixed-radix implementation is strictly subordinate to the scalar objective, so it cannot alter
documented economics. This is an observable plan-selection rule and therefore bumps
`POLICY_VERSION` from `reposition-v1` to `reposition-v2`.

The optimizer deliberately supports one inserted revenue Mission per structural empty-leg window.
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

### Knowledge-time boundary

The active Charter Graph is a current materialized projection rather than an arbitrary historical
snapshot. PR18 therefore reads the active projection checkpoint's `max_recorded_at` and exposes it
as `graph_knowledge_cutoff`.

If the requested `evaluated_at` precedes that cutoff, PR18 fails closed. It does not combine a
historical canonical Quote/profile cutoff with graph evidence that was learned later. Historical
optimization replay requires a future explicit as-of graph projection capability rather than
silently leaking future graph knowledge.

### Read-only execution

PR18 is a recommendation/query capability. It does not mutate Mission, Quote, Booking, aircraft,
Tender, or graph state.

The HTTP endpoint materializes and validates all canonical solver inputs inside a bounded:

```text
REPEATABLE READ, READ ONLY
```

transaction. The transaction is then closed before deterministic optimization begins. The detached
in-memory snapshot contains the graph projection identity/knowledge cutoff, structural empty legs,
canonical fleet snapshots, eligible Quote opportunities, and referenced airports required by the
solver. No repository or SQLAlchemy session handle crosses into the CPU optimization phase.

This preserves one coherent no-hindsight database snapshot without holding a pooled connection or
long MVCC snapshot during worst-case matching work. A future workflow that mutates authoritative
state from an advisory optimization result must revalidate the relevant canonical versions before
commit.

The endpoint continues to expose bounded limits for structural empty legs and quoted future
opportunities.

### Determinism and evidence

The optimizer is deterministic for the same:

- active graph projection and its knowledge checkpoint;
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
