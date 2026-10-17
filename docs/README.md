# CharterOS documentation

This directory contains durable product, architecture, assurance and operational documentation for the current CharterOS system.

## Architecture

- [Architecture atlas](architecture/README.md)
- [System context and containers](architecture/system-context.md)
- [Critical flows](architecture/critical-flows.md)
- [Authority and trust boundaries](architecture/authority-and-trust-boundaries.md)
- [Domain glossary](glossary.md)

## Security and integrity

- [Authentication and authorization](authentication-authorization.md)
- [Database-enforced evidence integrity](evidence-integrity.md)
- [Assurance index](assurance/README.md)
- [Data-governance classification](assurance/data-governance-classification.md)

## Operational assurance

The aircraft-commitment boundary is documented as one durable chain rather than as an implementation sequence:

- [ADR 0027 — Aircraft capacity reservations](adr/0027-aircraft-capacity-reservations.md)
- [ADR 0028 — Booking termination and capacity release](adr/0028-booking-termination-capacity-release.md)
- [ADR 0029 — Replacement-aircraft feasibility](adr/0029-replacement-aircraft-feasibility.md)
- [ADR 0030 — Reposition optimizer universe completeness](adr/0030-reposition-optimizer-universe-completeness.md)
- [ADR 0031 — Award-time feasibility truth gate](adr/0031-award-time-feasibility-truth-gate.md)
- [Aircraft commitment assurance](assurance/aircraft-commitment.md)

Additional durable assurance maps cover [event ordering/projection](assurance/event-ordering-and-projection.md), [reposition solver bounds](assurance/reposition-solver-boundary-and-performance.md), [determinism/clock behavior](assurance/determinism-and-clock.md), and [production readiness](assurance/production-readiness/README.md).

## Runbooks

Operational procedures live under [runbooks](runbooks/), including data governance, disaster recovery and release provenance. Runbooks define repository-owned responsibilities and explicitly distinguish them from provider-owned infrastructure behavior.

## Architectural decision records

ADRs under [adr](adr/) preserve the durable reasons behind important design choices. Their numeric identifiers are architectural record identities, not development milestones, and are intentionally retained.
