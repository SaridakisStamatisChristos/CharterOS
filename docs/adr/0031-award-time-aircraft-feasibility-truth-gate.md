# ADR 0031 — Award-Time Aircraft and Feasibility Truth Gate

## Status

Accepted for hardening PR42.

## Context

Before PR42, CharterOS revalidated commercial Quote state immediately before award and committed
aircraft capacity through the PR38 PostgreSQL exclusion constraint, but the selected tail was not
revalidated to the same depth at that final commitment boundary.

A Quote could therefore remain commercially valid while the quoted aircraft had become inactive,
moved to another operator, lost insurance/verification/commercial eligibility, become unavailable,
or moved to a position that made the Mission operationally infeasible.

PR40 already established a canonical single-aircraft Mission feasibility primitive. PR42 reuses it
rather than introducing a second aircraft suitability policy.

## Decision

Every award authority routes through `BookingService.accept_quote()`, including:

- direct Quote acceptance;
- Buyer Procurement Approval award;
- Tender award.

Immediately after Mission/RFQ/current-Quote commercial validation and immediately before capacity
commit, the award boundary now:

1. serializes the selected aircraft and its current operator row;
2. evaluates the exact quoted tail with the PR40 canonical `matching-v1` feasibility primitive at
   `known_as_of = award decision time`;
3. verifies that canonical aircraft/operator lineage still matches the quoted RFQ;
4. requires the aircraft to remain active and the operator verified, insured, and commercially
   active;
5. requires authoritative position and availability evidence;
6. rechecks seats, range, reposition distance, and reposition timing;
7. derives the PR38 `aircraft-capacity-v1` interval from the same decision time;
8. verifies capacity and matching reference evidence agree;
9. performs a read-only overlap precheck for an already committed reservation; and
10. creates the Booking and capacity reservation in the existing atomic transaction.

The overlap precheck is advisory-fast-fail only. The PR38 PostgreSQL GiST exclusion constraint
remains the concurrency-safe commitment authority, so simultaneous cross-Mission awards cannot both
commit the same tail into overlapping active capacity.

## No-hindsight semantics

Award revalidation uses the award decision timestamp as its `known_as_of` cutoff.

Earlier matching, quote-comparison, and procurement-approval evidence remains historical evidence and
is never rewritten because later operational facts changed.

Operational history that is recorded after the award decision cutoff is not retroactively injected
into that decision. Aircraft/operator current rows are serialized during the award transaction
because they are not bitemporal histories.

## Award decision evidence

A successful award appends one immutable `decision-evidence-v1` snapshot:

- decision type: `award_commit`;
- subject: Mission;
- source aggregate: created Booking;
- policy versions: `award-truth-gate-v1`, `matching-v1`, and
  `aircraft-capacity-v1`;
- Mission version;
- Quote ID, version, and revision;
- RFQ ID/version;
- aircraft/operator IDs and versions;
- position evidence;
- availability evidence;
- matching reference profile evidence;
- route/range/reposition/timing values;
- capacity interval;
- decision timestamp.

The snapshot is written in the same SQL transaction as Booking/reservation creation and Quote/Mission
state transitions. Any later failure rolls the evidence back with the award.

## Failure semantics

If aircraft-side truth changes after earlier matching or approval:

- award fails closed;
- no alternate aircraft is selected;
- no repricing or replacement Quote occurs;
- no Booking is created;
- no aircraft-capacity reservation is created;
- no award decision snapshot is created;
- no Quote-accepted or Mission-selected event is committed;
- historical approval/matching evidence remains intact.

## Preserved boundaries

PR42 does not change:

- Quote pricing or normalization;
- supplier ranking;
- reposition optimizer algorithms or tie-breaks;
- disruption replacement semantics;
- PR38 reservation lifecycle or exclusion constraint;
- PR39 release semantics;
- Mission/Quote state machines;
- existing evidence schema or database schema.

No migration is required.

## Consequences

Award is now the final commercial **and** operational truth gate. The aircraft committed into a
Booking is the exact tail that passed canonical Mission feasibility using authoritative award-time
evidence, while PostgreSQL remains the final resource-conflict authority.
