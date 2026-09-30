# ADR 0006 — Deterministic Matching Engine v1

## Status

Accepted for Roadmap PR6.

## Context

PR6 introduces the first production matching decision layer over canonical PR3–PR5 data. Matching must remain reproducible, explainable, bitemporal, exact-money safe, and independent of any LLM or graph datastore as transactional truth. The catalog does not yet carry validated cruise-speed or operating-cost reference values, so those inputs cannot be invented inside the scorer.

## Decision

`GET /v1/missions/{id}/matches` is a side-effect-free read decision. Only `OPEN` missions are eligible in v1. PostgreSQL remains canonical and each request executes under a read-only `REPEATABLE READ` transaction.

The pipeline is:

1. Load the persisted mission and canonical airports.
2. Resolve an explicit `known_as_of` knowledge cutoff (the injected authoritative UTC clock when omitted).
3. Build one bounded PostgreSQL candidate snapshot (maximum 2,000 aircraft) using lateral as-of lookups. Position facts require `recorded_at <= known_as_of`; availability and matching-profile authority are also evaluated as of the same cutoff.
4. Apply hard filters before any ranking.
5. Rank only feasible candidates with a deterministic, decomposed score.

### Hard feasibility policy

A candidate must satisfy all of the following:

- aircraft status is `ACTIVE`;
- operator verification is `VERIFIED`;
- operator insurance is `VALID`;
- operator commercial status is `ACTIVE`;
- seat capacity is at least the mission passenger count;
- aircraft range is at least great-circle route distance plus a 10% deterministic reserve;
- one authoritative `AVAILABLE` interval known by the cutoff covers the full half-open mission departure window;
- a usable latest position observation is known by the cutoff;
- a matching reference profile is known by the cutoff;
- reposition distance does not exceed the profile's explicit maximum;
- reposition flight time plus the profile turnaround buffer can reach the origin by the end of the mission departure window.

### Matching reference data

`matching_reference_profiles` is a narrow, provenance-aware reference-data boundary keyed by aircraft type. Each profile records cruise speed in knots, exact operating cost per hour in minor units and currency, maximum reposition distance in nautical miles, turnaround buffer minutes, source, provenance, knowledge timestamp, and optional supersession timestamp.

PR6 intentionally provides no public mutation API for this table. Validated reference data can be governed/imported separately. Missing data produces an explicit infeasibility reason; no hidden default is used. The v1 ranking reference currency is EUR. A non-EUR profile is rejected explicitly rather than converted with an assumed FX rate. Mission budgets are informational in PR6: same-currency budget comparisons are exact; mismatched currency is surfaced explicitly and never silently converted.

### Distances, timing, and exact money

Great-circle route and reposition distances use one centralized Haversine implementation over
WGS84-style latitude/longitude inputs. Before trigonometry, coordinates are canonicalized to
`0.000001` degree using decimal `ROUND_HALF_UP`. Haversine/libm remains a floating-point
geometric calculation; its result is stabilized to `0.000001` nautical mile before the
business-visible `0.1` nautical-mile `ROUND_HALF_UP` boundary is applied. This explicitly
separates unavoidable transcendental floating computation from the discrete eligibility unit and
prevents insignificant platform-level libm noise from flipping a boundary decision.

The same distance implementation is used by matching and repositioning. No Decimal trigonometry or
fake precision is claimed. Cruise duration is integer minutes rounded upward. Operating cost is
computed from exact integer minor units and integer minutes, rounded upward; binary floating-point
is never used for currency.

### Ranking

Only feasible candidates are scored. The four required dimensions have equal 25% weight:

- lower reposition distance;
- lower estimated operating cost;
- greater timing buffer;
- lower schedule-risk ratio.

Each component is min-max normalized within the feasible candidate set to 0–2,500 points. Equal component values receive full component points. Total score is the sum, with a maximum of 10,000 basis points. The response exposes every raw and weighted component.

Stable tie-breaking is: higher total score, then lower deadhead, then greater timing buffer, then lexical aircraft UUID. No random tie-breaking is allowed.

### Knowledge time / no hindsight

For cutoff `K`, a position recorded after `K` is invisible even when its event time is earlier. Availability corrections and profile supersessions are authoritative only according to what was known at `K`. The position event cutoff is `min(K, departure_window.start)`. Repeating a historical match with the same mission and cutoff therefore cannot be changed by a later-recorded observation.

### Charter Graph / NetworkX boundary

The Charter Graph testbench remains a deterministic reference/regression model. Its hard-filter-before-ranking and no-hindsight invariants are preserved, but its synthetic economics are not copied. NetworkX may be used later for bounded computation or regression work; it is not a canonical store in PR6.

## Consequences and v1 limitations

PR6 does not yet model crew duty, maintenance programs beyond aircraft status/availability, runway performance, slots, curfews, permits, ETOPS, future rotations, stochastic disruption, or live market pricing. Those omissions are explicit and do not become hidden penalties. PR6 also does not create RFQs, quotes, bookings, or matching mutation events.
