# ADR 0019 — Deterministic Market Simulator

Status: Accepted

Date: 2026-09-29

## Context

CharterOS needs a market simulator before any historical pricing-intelligence dataset or production ML work. The simulator must create repeatable synthetic scenarios for demand, quote behavior, operator decisions, conversion, aircraft continuity, empty legs, and completed missions without contaminating canonical production truth.

PR19 follows the authority boundaries established through PR18:

- PostgreSQL/domain aggregates remain canonical transactional truth.
- The Charter Graph remains an event-derived, rebuildable projection.
- PR18 remains the deterministic, read-only repositioning/deadhead optimizer for production evidence.
- Tender confidentiality remains enforced across alternate read paths.
- Money remains signed-int64 minor units with explicit \`Currency\`.
- No implicit FX exists before PR26.

## Decision

Implement PR19 as a pure, non-persistent simulation module under \`charteros.simulation\`.

The simulator does not write Mission, RFQ, Quote, Booking, outbox, graph, tender, or optimization records. Its return value is an immutable \`MarketSimulation\` envelope whose records are explicitly marked as synthetic simulation evidence.

## 1. Purpose and non-goals

The simulator generates deterministic synthetic market scenarios for engineering, testbench, and future analytics work.

It is not:

- empirical production market truth;
- a pricing recommendation engine;
- a buyer award engine;
- a production operator decision score;
- an ML model;
- a persistence path for canonical business state;
- an FX subsystem;
- PR20 historical intelligence.

## 2. Simulation authority boundary

All PR19 output is simulation-only evidence.

Every top-level generated record carries:

- \`scenario_id\`;
- \`synthetic = true\`;
- \`evidence_kind = synthetic_market_simulation\`.

The top-level scenario also carries seed, simulator policy version, full configuration, deterministic reference-state digest, simulation period, and scenario digest.

No PR19 code is wired into production repositories, the transactional outbox, the Charter Graph projection, Tender state, Booking state, or PR18 optimizer mutation paths.

## 3. Seed and determinism contract

The simulator policy version is:

\`\`\`text
market-sim-v1
\`\`\`

For the same effective:

\`\`\`text
policy version
seed
configuration/reference state
\`\`\`

PR19 produces the same scenario.

Random-looking outcomes do not consume process-global RNG state. \`DeterministicSampler\` addresses every draw by SHA-256 over:

\`\`\`text
policy_version + seed + semantic_label
\`\`\`

Examples of semantic labels include demand/day/route, operator/demand acceptance, quote response, quote revision volatility, conversion, and completion.

This prevents call-order coupling: adding an unrelated sample cannot shift an existing labeled outcome.

The seed must be a non-negative signed-int64 integer.

## 4. Simulator identity and configuration identity

\`MarketSimulationConfig\` is immutable and included in the returned scenario. It contains the explicit simulation clock start, horizon, airport reference points, routes, currencies, demand/seasonal inputs, operator/aircraft profiles, decision probabilities, volatility bounds, and execution limits.

\`reference_state_digest\` is a canonical SHA-256 digest of that configuration.

\`scenario_id\` is deterministic UUIDv5 over policy version, seed, and reference-state digest.

\`scenario_digest\` is a canonical SHA-256 digest over the generated envelope with the digest field blanked during hashing. It is intended for replay/regression assertions, not as a cryptographic authenticity claim.

## 5. Simulation clock and time semantics

\`start_at\` must be timezone-aware and is normalized to UTC.

The configured \`days\` interval is the demand-generation period and is represented as a half-open \`TimeRange\`:

\`\`\`text
[start_at, start_at + days)
\`\`\`

Demand creation time, lead time, departure windows, operator decision time, quote revision time, conversion time, empty-leg timing, and completion timing derive only from the explicit simulation clock/configuration and deterministic samples.

No market outcome depends on \`datetime.now()\` or wall-clock execution time.

Generated missions may complete after the demand-generation period when explicit lead time and flight time carry them beyond that boundary.

## 6. Demand-curve model

Each route defines:

- a base daily demand count;
- explicit monthly demand multipliers in parts per million;
- passenger bounds;
- lead-time bounds.

For each route/day, the simulator records a \`DemandCurvePoint\` containing the seasonal multiplier, deterministic demand-noise multiplier, and generated count.

Fractional fixed-point expected demand is resolved by a deterministic labeled draw. Demand generation is bounded before execution using a conservative upper-bound check and again during generation by \`max_generated_demands\`.

Synthetic demand curves are not labeled or represented as empirical calibration.

## 7. Seasonal model

Seasonality is an explicit twelve-element monthly multiplier vector per route.

Demand seasonality and price seasonality are separate inputs. This makes changes explainable and avoids embedding opaque or empirically implied seasonal behavior in PR19.

All seasonal arithmetic is integer fixed-point arithmetic in parts per million.

## 8. Quote response and volatility model

An operator can quote only when:

- route currency equals operator currency;
- passenger capacity is sufficient;
- route range is sufficient;
- reposition distance/range is sufficient;
- reposition timing fits the half-open departure window;
- its deterministic acceptance decision passes;
- its deterministic quote-response decision passes.

Quoted totals use exact \`Money\` minor units and the route currency. No floating-point money is introduced.

Quote volatility is an explicit bounded signed parts-per-million adjustment. Optional quote revisions are modeled as immutable ordered revision tuples with contiguous revision numbers. Later revisions never rewrite earlier revisions.

The simulator does not claim that these synthetic quote objects are canonical production Quote aggregates or bypass production normalization. They represent synthetic all-in comparable totals inside one explicit route currency.

## 9. Operator acceptance/rejection model

Each operator decision records:

- configured/effective acceptance probability;
- deterministic acceptance draw;
- accepted/rejected result;
- reason;
- when accepted, quote-response probability, draw, and response result.

Schedule, capacity, range, or currency failure sets effective acceptance probability to zero and records the concrete reason.

These probabilities are simulation inputs, not production operator scores.

## 10. Conversion model

For a demand with one or more same-currency synthetic quotes, conversion uses an explicit bounded probability and deterministic draw.

If conversion succeeds, selection is deterministic:

\`\`\`text
lowest current quoted total
then quote UUID as stable tie-breaker
\`\`\`

There is no cross-currency ranking. Mismatched operator currencies fail closed before quote generation.

The conversion record also preserves completion probability/draw/result when a quote was selected, so the causal downstream outcome is replayable.

PR19 never uses conversion to auto-award canonical production procurement.

## 11. Repositioning and empty-leg relationship to PR16/PR18

PR19 does not call or alter the production PR18 optimization service.

It reuses PR18-compatible matching primitives already used by the optimizer:

- great-circle distance via \`haversine_distance_tenths_nm\`;
- range reserve via \`required_range_nm\`;
- flight duration via \`flight_minutes\`;
- turnaround-aware schedule continuity.

Each simulated operator owns one explicit synthetic aircraft state: current airport and next available time. A successful completed mission updates that state. When the next completed mission starts elsewhere, the simulator emits a \`SimulatedEmptyLeg\` representing the required deadhead/reposition movement.

The empty leg must fit distance, range, and time constraints before the revenue mission can be quoted/completed. This preserves aircraft continuity without making the production Charter Graph or PR18 optimizer stochastic.

## 12. Completed-Mission generation

The causal chain is explicit:

\`\`\`text
demand
-> operator decision
-> quote response/revision
-> conversion and deterministic quote selection
-> completion decision
-> optional reposition/empty leg
-> completed simulated mission
\`\`\`

A \`SimulatedCompletedMission\` cannot exist without a converted demand, selected quote, selected operator/aircraft, and successful completion draw.

Completion times derive from the selected departure and the established flight-time primitive. Completed records remain synthetic and create no production settlement, invoice, disruption, or reconciliation evidence.

## 13. Money, currency, and FX boundary

PR19 preserves:

- signed-int64 minor-unit \`Money\`;
- explicit \`Currency\`;
- integer fixed-point probability/seasonality/volatility math;
- no floating-point money;
- no hidden rate source;
- no FX conversion;
- no cross-currency global ranking.

An operator with a different currency from the demand route is rejected with \`currency_mismatch\`.

PR26 remains the only roadmap task authorized to introduce auditable FX conversion/ranking.

## 14. Synthetic-data provenance and isolation

PR19 intentionally has no persistence adapter.

The simulator does not emit canonical domain events and does not append to the transactional outbox. It cannot create graph-projection evidence, Tender evidence, Booking evidence, or canonical Mission/Quote records by ordinary execution.

Callers must treat its return value as synthetic test/simulation data. If a later PR persists simulation scenarios, the persistence model must retain scenario identity, policy version, seed, configuration/reference digest, clock period, and synthetic marker rather than reusing production evidence tables indistinguishably.

## 15. Testing and replay semantics

PR19 tests cover:

- same seed/config replay equality and digest equality;
- different-seed deterministic divergence;
- semantic-label sampling independent of call order;
- explicit seasonal boundary changes;
- deterministic quote revisions/volatility;
- exact minor-unit money and currency consistency;
- no implicit FX/cross-currency quote path;
- conversion/completion causal linkage;
- aircraft continuity and empty-leg linkage;
- synthetic/provenance markers;
- no wall-clock dependency;
- bounded pathological-demand rejection.

The full PR1-PR18 regression suite remains authoritative in CI.

## 16. Explicit PR20 boundary

PR19 does not build a historical pricing dataset, analytics warehouse, feature store, learned pricing model, or production ML path.

PR20 may consume explicitly synthetic scenarios for testbench purposes, but must distinguish them from historical production evidence. Any feature-ready historical intelligence remains PR20 work.

## Consequences

### Positive

- Fully repeatable synthetic market scenarios.
- Strong isolation from production truth.
- Decision-level replay evidence rather than opaque randomness.
- Existing aircraft geometry/range/time semantics are reused.
- Exact-money/no-FX invariants survive simulation.
- Future PR20 work receives a deterministic test data source.

### Trade-offs

- PR19 is deliberately a model, not empirical calibration.
- One synthetic aircraft is modeled per simulator operator in v1.
- Seasonality is explicit monthly configuration rather than a learned curve.
- No multi-currency normalization is attempted before PR26.
- No persistence/API surface is added in PR19, keeping the synthetic boundary narrow.
