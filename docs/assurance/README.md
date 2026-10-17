# Assurance documentation

This directory contains durable engineering assurance material for CharterOS. It documents correctness boundaries, reproducible verification methods, and operational evidence that remain useful independent of the sequence in which the implementation was developed.

## Core assurance maps

- [Event ordering and graph projection](event-ordering-and-projection.md) — aggregate causality, outbox delivery, projection cursors, replay and convergence.
- [Reposition solver boundary and performance](reposition-solver-boundary-and-performance.md) — deterministic optimizer scope, benchmark contract and validated workload envelope.
- [Determinism and clock](determinism-and-clock.md) — temporal authority and deterministic distance semantics.
- [Aircraft commitment](aircraft-commitment.md) — award-time feasibility, capacity reservation, release, replacement feasibility and optimizer completeness.
- [Production readiness](production-readiness/README.md) — observability, recovery, provenance and operational qualification evidence.
- [Data governance classification](data-governance-classification.md) — data categories and governance assumptions.

## Reading rule

Assurance documents describe implemented invariants and verified boundaries. They are not release notes, deployment certification, or evidence that a live environment is meeting production SLOs. When an assurance document references an ADR, the ADR remains the durable architectural decision record.
