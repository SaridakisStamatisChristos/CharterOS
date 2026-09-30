# ADR 0030 — Reposition Optimizer Input-Universe Completeness

## Status

Accepted for hardening PR41.

## Context

The deterministic reposition optimizer is validated for at most 100 structural empty-leg candidates
and 2,000 quoted future-leg opportunities.

Before PR41, the quoted-opportunity repository already queried `limit + 1` and failed when an
additional row proved the input universe incomplete. Structural empty-leg discovery did not have the
same property. The graph repository could return:

```python
tuple(candidates[:limit])
```

and the optimizer could therefore receive exactly 100 structural candidates even when 101 or more
existed. A plan generated from that incomplete universe could still report
`global_plan_available=true`, which overstated what had actually been optimized.

## Decision

Optimizer input completeness is now explicit and fail-closed.

For structural empty legs:

- public graph browsing keeps its bounded-list behavior;
- optimizer reads set `require_complete=True`;
- that path asks the graph repository for one additional candidate beyond the requested bound;
- 0 through the requested bound are accepted;
- observation of the extra item raises an application conflict instead of truncating.

At the default validated envelope:

```text
0..100 structural candidates -> complete and allowed
101+ structural candidates   -> fail closed
```

For quoted future legs, the existing SQL `LIMIT limit + 1` behavior remains authoritative. PR41
routes its overflow result through the same bounded-universe guard used by structural discovery.

At the default validated envelope:

```text
0..2000 quoted opportunities -> complete and allowed
2001+ quoted opportunities   -> fail closed
```

The application-level error contract includes the reposition policy version, the validated capacity,
the requested capacity, an `observed_count_at_least` lower bound, and a stable reason code. For the
canonical structural limit the reason is:

```text
structural_candidate_universe_exceeds_validated_capacity
```

The optimizer never claims a global plan from a universe that it already knows is incomplete.

## Capacity ownership

PR36 remains the source of truth for the validated capacity envelope:

- `MAX_STRUCTURAL_EMPTY_LEGS = 100`
- `MAX_QUOTED_FUTURE_LEGS = 2_000`

The HTTP schema continues to expose those same maxima. PR41 does not raise either capacity and adds
no new benchmark claim.

A caller may request a smaller bound. If the universe exceeds that requested bound, optimization also
fails closed rather than silently optimizing only the prefix.

## Non-goals

PR41 does not change:

- the Hungarian assignment algorithm;
- `reposition-v2` tie-break semantics;
- route, timing, range, or economic calculations;
- graph projection semantics;
- the graph browsing API's bounded-list behavior;
- the validated 100 / 2,000 capacity envelope;
- Booking, Quote, Tender, FX, or capacity-reservation state.

## Consequences

The structural and quoted sides of the optimizer now have symmetric completeness semantics. Bounded
browse APIs remain usable as bounded views, while the global optimizer refuses to produce an
apparently complete recommendation from a knowingly truncated candidate universe.
