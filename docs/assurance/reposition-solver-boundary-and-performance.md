# Reposition solver boundary and performance assurance

This document defines the verified scope of CharterOS's deterministic reposition/deadhead optimizer and the evidence required to change that scope.

## Authority boundary

The optimizer is decision support. It reads canonical mission, quote, fleet and continuity evidence and returns a deterministic recommendation; it does not award a quote, reserve aircraft capacity or mutate procurement state.

The economic objective is continuity-adjusted margin. Canonical tie handling is deterministic and may not override that objective.

## Exact solver

The current reposition policy uses an exact shortest-augmenting-path Hungarian assignment with deterministic canonical tie semantics. Candidate generation is bounded before the assignment stage and must fail closed when the configured universe is known to be incomplete.

## Validated workload envelope

The application/API contract is currently qualified for:

| Dimension | Bound |
| --- | ---: |
| Structural empty legs | 100 |
| Quoted future legs | 2,000 |
| Approximate dense candidate edges | 200,000 |

A workload above these bounds is unvalidated, not mathematically declared invalid. Raising a bound requires new benchmark and correctness evidence.

## Benchmark contract

`tools/benchmark_reposition_solver.py` is a deterministic regression benchmark. CI uses it to detect algorithmic/behavioral regression without pretending that a shared hosted runner provides a stable production latency SLO.

Performance claims must therefore distinguish:

- exactness and deterministic equivalence, which are correctness properties;
- the repository's measured benchmark cases;
- live production latency/capacity, which requires environment-specific evidence.

See [ADR 0018](../adr/0018-repositioning-deadhead-optimizer.md) and [ADR 0030](../adr/0030-reposition-optimizer-universe-completeness.md).
