# PR32 — Solver Transaction Boundary and Performance Assurance

## Scope

Production Hardening PR32 addresses two separate risks in the PR18 repositioning optimizer:

1. the database transaction previously remained open while CPU-bound optimization ran; and
2. the exact residual min-cost-flow implementation repeatedly relaxed the full residual graph for
   every augmentation, which scaled poorly at the bounded dense workload.

The hardening does not change the canonical business authority, optimization economics, no-hindsight
rules, currency boundary, feasibility policy, or one-empty-leg/one-mission assignment model.

## Transaction boundary

The HTTP path now executes in two phases.

### Phase 1 — bounded canonical snapshot

Inside the existing PostgreSQL transaction:

```text
REPEATABLE READ, READ ONLY
```

CharterOS:

- reads the active graph projection identity and knowledge cutoff;
- discovers bounded structural empty-leg candidates;
- validates booking lineage;
- materializes canonical aircraft/operator matching snapshots;
- materializes eligible quoted future legs;
- materializes every referenced airport; and
- validates graph/canonical ownership and no-hindsight invariants.

The result is a frozen `RepositionOptimizationSnapshot` containing only detached in-memory domain
objects. No SQLAlchemy session or repository handle crosses this boundary.

### Phase 2 — deterministic CPU optimization

The transaction exits before `optimize_reposition_snapshot()` runs. The route has an explicit
regression guard that fails if SQLAlchemy still reports an active transaction at that point.

A future command that commits authoritative state from this advisory result must revalidate the
relevant canonical versions before mutation.

## Exact solver implementation

The PR18 objective remains maximum positive continuity-adjusted margin with deterministic
candidate-order tie costs. Capacity remains:

```text
one structural empty-leg window -> at most one Mission
one Mission                     -> at most one structural empty-leg window
```

PR32 replaces repeated Bellman-Ford residual relaxation with a deterministic shortest-augmenting-path
Hungarian assignment over the same encoded weights. Unmatched rows receive zero-weight dummy
columns. Missing real edges are never treated as feasible assignments.

For the hard PR18 bounds of at most 100 structural gaps and 2,000 candidate Missions, the assignment
core is bounded by approximately:

```text
O(L^2 * (R + L))
```

rather than repeatedly scanning every residual edge for every augmentation.

Parallel candidates with the same gap/Mission endpoints are reduced only to the edge with the best
already-encoded objective value; two such edges cannot both participate in a feasible assignment.

## Optimality and determinism assurance

Unit assurance preserves the existing greedy-counterexample regression and adds:

- brute-force comparison of the encoded objective over deterministic sparse matrices from 1x1
  through 4x4;
- equal-margin contention repeated ten times to verify deterministic output; and
- an explicit route regression proving the CPU solver observes no active database transaction.

### PR32.1 tie-break correction

The original PR32 implementation preserved the PR18 scalar objective:

1. maximize aggregate continuity-adjusted margin; then
2. minimize the aggregate candidate-index penalty.

That scalar objective is not a strict total order. Different feasible assignment sets can have the
same total margin and the same sum of candidate indices. In those cases the legacy residual-flow
solver and the PR32 Hungarian solver could select different plans even though both were exactly
optimal under the documented scalar objective.

`reposition-v2` makes the final tie explicit and solver-independent:

3. among equal scalar objectives, choose the lexicographically smallest mission-rank vector over
   sorted structural empty-leg keys; an unmatched row is ordered after every real Mission.

Mission rank is the index in the existing sorted Mission UUID key order. The implementation encodes
this lexicographic component in a mixed-radix integer whose total range is strictly smaller than one
unit of the existing scalar objective. It therefore cannot change margin economics or the existing
candidate-index penalty.

The policy version is bumped from `reposition-v1` to `reposition-v2` because exact-tie plan
selection is observable output semantics. Historical evidence recorded under v1 remains v1; a replay
under v2 may intentionally differ only for previously under-specified exact ties.

A checked-in golden fixture,
`tests/golden/reposition_solver_tie_break_v2.json`, captures a minimal collision where two plans
have equal margin and equal scalar tie penalty. The test locks the canonical v2 plan and verifies
that input candidate permutation cannot change it.

The complete repository suite remains the authority for PR18 feasibility, economics, graph
knowledge-time behavior, API output, and integration semantics.

## Performance benchmark

The repository contains `tools/benchmark_reposition_solver.py` and CI executes it after the full
test suite. It uses actual `FeasibleInsertion` DTOs and the production
`maximum_margin_matching()` function.

The benchmark is deterministic and intentionally synthetic. Its `data_load_ms` field means
construction of immutable in-memory DTOs; it **does not** measure PostgreSQL query/materialization
latency. Timings below are instrumented with Python `tracemalloc` enabled so peak Python allocation
can be recorded; they are therefore conservative relative to an uninstrumented timing run.

Representative GitHub Actions evidence from CI #355 / run `36691518011`:

| Left nodes | Right nodes | Nodes | Candidate edges | DTO load ms | Solver ms | Total ms | Traced peak MiB | Selected |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 100 | 110 | 1,000 | 47.492 | 24.125 | 71.627 | 0.954 | 10 |
| 50 | 500 | 550 | 25,000 | 1,031.598 | 671.650 | 1,703.255 | 21.230 | 50 |
| 100 | 2,000 | 2,100 | 200,000 | 9,154.176 | 5,623.264 | 14,777.464 | 168.009 | 100 |

The external evaluation summarized in the hardening handoff reported approximately 18 seconds at
about 200,000 edges while the database transaction remained open. PR32 changes the operationally
important part of that finding even independently of solver speed: the database transaction is no
longer held during these seconds of CPU work. The benchmark also shows the production solver phase
itself completing the 200,000-edge synthetic case in 5.623 seconds on the CI runner under allocation
tracing.

These numbers are repository assurance evidence, not a production SLO or capacity certification.
Real database materialization latency, concurrent load, target hardware, managed PostgreSQL
behavior, and end-to-end service capacity must be measured separately during deployment
certification.

## CI policy

The benchmark is evidence-producing rather than a wall-clock pass/fail threshold. CI runners have
variable shared-host performance, so an arbitrary timing cutoff would create a flaky gate. Exact
correctness remains hard-gated by tests; performance results remain visible in every CI log for
trend comparison.
