# ADR 0029 — Canonical replacement-aircraft feasibility

**Status:** Accepted  
**Date:** 2026-09-30

## Context

The PR23 disruption workflow intentionally prevented cross-operator replacement from becoming an alternate award path, but a same-operator replacement tail was only checked for canonical fleet ownership, active state, commercial operator state, and authoritative availability.

That was weaker than normal Mission matching. An aircraft could therefore be available yet still be unsuitable because of seats, range, missing position/reference evidence, operator verification or insurance, or reposition distance/timing.

PR38 and PR39 separately established aircraft-capacity reservations and deterministic release. PR40 must reuse those boundaries rather than introduce a second inventory authority.

## Decision

A new `AircraftMissionFeasibilityService` provides the single-aircraft application view of the existing `matching-v1` policy.

It:

- loads the same bitemporal matching snapshot used by normal matching;
- uses an explicit `known_as_of` decision time;
- applies the same `evaluate_candidate()` hard constraints and deterministic route math;
- permits an explicit disruption departure window without changing normal matching defaults;
- retains proposal-time aircraft/operator versions plus position, availability, reference-profile, route, range, reposition, and timing evidence;
- fails closed when the canonical snapshot is missing or infeasible.

Normal matching continues to call the same pure `evaluate_candidate()` primitive, so PR40 does not create a second seat/range/reposition policy.

## Capacity semantics

Replacement proposal is **not** a capacity commitment.

At proposal time CharterOS derives the canonical `aircraft-capacity-v1` interval and performs a read-only overlap check against active `reserved` capacity, excluding the Booking's own reservation. It does not insert, hold, mutate, or release a reservation.

Because a read-only check can race with another commitment, material replacement feasibility and capacity are checked again at disruption resolution. PostgreSQL's PR38 exclusion constraint remains the only authoritative commitment boundary; PR40 does not weaken or bypass it.

A future workflow that actually commits a replacement aircraft must perform its reservation mutation atomically at that commitment point.

## Historical evidence / no hindsight

Proposal evidence is immutable historical decision evidence. Later availability or position corrections do not rewrite the proposal.

Resolution re-evaluates against the evidence authoritative at the later resolution time. Therefore a tail may have been feasible when proposed but fail resolution after a newly recorded authoritative correction. The earlier proposal remains intact.

## Compatibility

Existing legacy/non-material disruption proposals may have no PR40 feasibility evidence. New material aircraft/window replacement proposals created through the application service always carry the complete PR40 evidence set.

Cross-operator replacement continues to require canonical re-procurement. Disruption handling remains evidence/remediation workflow, not Booking or award authority.

## Consequences

- Same-operator replacement now has the same physical feasibility rules as normal matching.
- PR23 disruption lineage, buyer-decision binding, Quote immutability, and outbox semantics remain unchanged.
- PR38 reservation state remains `reserved -> released`; no `held` state is introduced.
- PR42 can reuse the same single-aircraft feasibility service for final award-time truth-gating.
