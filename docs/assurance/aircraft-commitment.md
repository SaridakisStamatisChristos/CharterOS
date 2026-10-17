# Aircraft commitment assurance

Aircraft commitment correctness is a composed boundary spanning award-time feasibility, database-enforced capacity, eligible release, disruption recovery and optimizer completeness.

## Commitment invariant

A quote, approval, match or optimization result does not reserve aircraft capacity. The authoritative commitment occurs only through the booking-award transaction after the exact quoted aircraft has been revalidated.

## Capacity reservation

[ADR 0027](../adr/0027-aircraft-capacity-reservations.md) defines aircraft-capacity reservations. PostgreSQL is the final overlap authority: active reservation intervals for one aircraft are protected by a database exclusion constraint, and booking plus reservation are committed atomically with their domain events.

## Release

[ADR 0028](../adr/0028-booking-termination-capacity-release.md) defines eligible booking termination and coupled capacity release. Release is an explicit state transition and does not erase the original award history.

## Replacement feasibility

[ADR 0029](../adr/0029-replacement-aircraft-feasibility.md) defines replacement-aircraft feasibility during disruption handling. It reuses canonical `matching-v1` policy and performs a read-only capacity check. A replacement recommendation does not itself create a reservation or a second award authority. Cross-operator recovery remains a procurement concern.

## Optimizer completeness

[ADR 0030](../adr/0030-reposition-optimizer-universe-completeness.md) defines the optimizer's bounded input-universe contract. The optimizer fails closed when completeness cannot be established inside the validated envelope rather than returning an apparently exact answer over an incomplete universe.

## Award-time truth gate

[ADR 0031](../adr/0031-award-time-feasibility-truth-gate.md) defines the final award authority. `BookingService.accept_quote` revalidates the exact quoted tail and applicable capacity interval immediately before the authoritative transaction. Decision evidence is stored with the committed award path.

## Authority summary

| Concern | Authority |
| --- | --- |
| Supplier/aircraft recommendation | Deterministic matching |
| Pre-award buyer decision | Procurement approval evidence |
| Final aircraft feasibility | Award-time feasibility check |
| Capacity overlap | PostgreSQL exclusion constraint |
| Booking/capacity commit | BookingService transaction |
| Eligible release | Booking termination transaction |
| Replacement recommendation | Disruption feasibility using matching policy |
| Reposition recommendation | Deterministic optimizer |

These mechanisms are tested as one correctness chain. They do not imply that a recommendation, graph projection or optimization result has mutation authority.
