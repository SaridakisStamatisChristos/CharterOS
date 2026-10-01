# PR38–PR42 adversarial hardening lineage

PR38 through PR42 form one connected correctness chain around aircraft commitment, release,
replacement, optimization completeness, and final award authority. Each PR has its own ADR; this
document explains how the five decisions compose.

## Why this chain matters

Before this hardening sequence, several individual workflows were locally correct but did not yet
share one fully explicit end-to-end resource/feasibility boundary. The sequence closes those gaps
without creating a second Booking authority, matching policy, capacity authority, optimizer policy,
or disruption award path.

The resulting invariant is:

> A CharterOS award may commit an aircraft only if the exact quoted tail is operationally feasible
> at award time and PostgreSQL can atomically reserve the resulting aircraft-capacity interval. Any
> later pre-confirmation termination releases that capacity atomically. Replacement and optimizer
> workflows may reason about capacity and feasibility, but they cannot silently become alternate
> commitment authorities.

## PR38 — PostgreSQL-enforced aircraft capacity reservations

Design authority: [ADR 0027](../adr/0027-aircraft-capacity-reservations.md).

PR38 introduced the first-class `AircraftCapacityReservation` resource aggregate and
`aircraft-capacity-v1`.

The reserved interval is deterministically derived from:

- Mission departure window;
- canonical matching route duration;
- matching-reference-profile turnaround buffer.

PostgreSQL is the final overlap authority through a partial GiST exclusion constraint over the
aircraft and active half-open time range.

Award creates the Booking and reservation in the same transaction. If reservation insertion
conflicts, the entire award rolls back: no Booking, Quote acceptance, Mission selection, reservation,
idempotency result, decision/evidence append, or award event survives.

This closes cross-Mission same-tail double booking under concurrency.

## PR39 — atomic Booking termination and capacity release

Design authority: [ADR 0028](../adr/0028-booking-termination-capacity-release.md).

PR39 made the PR38 resource boundary operationally reusable by adding deterministic pre-confirmation
Booking termination.

Canonical terminal reasons are:

- `contract_unsigned`;
- `deposit_timeout`;
- `commercial_expiry`;
- `buyer_cancel`;
- `operator_release`.

The command atomically transitions:

- Booking terminal state;
- Mission terminal state;
- PR38 reservation `reserved -> released`;
- idempotency result;
- outbox/domain evidence.

Competing terminal commands serialize on the Booking. Confirmed and later operational Booking states
remain outside this release path.

This prevents ghost capacity while preserving the reservation as a resource aggregate rather than a
second Booking lifecycle.

## PR40 — canonical replacement-aircraft feasibility

Design authority: [ADR 0029](../adr/0029-canonical-replacement-aircraft-feasibility.md).

PR40 closed the same-operator disruption replacement gap by introducing a focused
`AircraftMissionFeasibilityService` that reuses the canonical `matching-v1`
`evaluate_candidate()` primitive.

Material same-operator replacement proposals therefore fail closed on the same underlying
feasibility evidence used by normal matching, including:

- aircraft active state;
- operator verification, insurance, and commercial status;
- operator/aircraft lineage;
- seats and range;
- authoritative availability;
- position evidence;
- matching reference profile;
- reposition distance and timing.

Proposal-time evidence is retained with the applicable decision time and source versions.

A replacement proposal is **not** a capacity commitment. PR40 performs a read-only overlap check
against active PR38 reservations and revalidates at disruption resolution, but it does not create a
temporary hold or alternate reservation state. Cross-operator replacement remains canonical
re-procurement.

This preserves no-hindsight evidence while ensuring disruption replacement cannot bypass normal
physical feasibility.

## PR41 — fail closed on incomplete optimizer input universe

Design authority: [ADR 0030](../adr/0030-reposition-optimizer-input-completeness.md).

PR41 fixed a correctness issue specific to global optimization: a bounded prefix is acceptable for a
browse API, but not for a solver that would otherwise present the result as a complete global plan.

The validated optimizer envelope remains:

- at most 100 structural empty-leg candidates;
- at most 2,000 quoted future-leg opportunities.

Optimizer reads probe one item beyond the requested/validated bound. If an additional item proves the
universe is incomplete, optimization fails closed instead of silently truncating.

Ordinary graph browsing keeps bounded-prefix semantics.

PR41 does not change the Hungarian assignment algorithm, economics, tie-break rules, graph
projection, or capacity reservation behavior. It strengthens only the truthfulness of the solver's
input-completeness claim.

## PR42 — award-time aircraft feasibility truth gate

Design authority:
[ADR 0031](../adr/0031-award-time-aircraft-feasibility-truth-gate.md).

PR42 makes award the final commercial **and operational** truth gate.

Every award authority converges on `BookingService.accept_quote()`:

- direct Quote acceptance;
- Buyer Procurement Approval award;
- Tender award.

Immediately before capacity commitment, CharterOS revalidates the exact quoted tail using the PR40
canonical feasibility primitive at the award decision time. It rechecks:

- quoted aircraft/operator lineage;
- aircraft active state;
- operator verification, insurance, and commercial state;
- position and availability evidence;
- seats and range;
- reposition distance and timing;
- matching-reference evidence;
- PR38 capacity interval.

A read-only overlap precheck can fail fast, but the PR38 PostgreSQL exclusion constraint remains the
concurrency-safe final commitment authority.

A successful award appends immutable `decision-evidence-v1` with
`award-truth-gate-v1`, `matching-v1`, and `aircraft-capacity-v1` provenance in the same
transaction as Booking/reservation/Quote/Mission state.

If aircraft-side truth changes after an earlier approval, award fails closed while the earlier
approval remains historical evidence.

## Composed authority chain

```text
matching / procurement evidence
        |
        v
PR42 award-time exact-tail feasibility
        |
        v
PR38 deterministic capacity interval
        |
        v
PostgreSQL GiST exclusion constraint
        |
        v
atomic Booking + reservation + award evidence
        |
        +-----------------------------+
        |                             |
        v                             v
PR39 pre-confirmation            confirmed/later state
termination + release            no PR39 release shortcut

PR40 disruption replacement:
feasibility + read-only capacity check
(no commitment authority)

PR41 optimizer:
complete bounded universe or fail closed
(no commitment authority)
```

## Authority table

| Concern | Final authority |
| --- | --- |
| Aircraft physical suitability | canonical `matching-v1` feasibility primitive |
| Award-time operational truth | `BookingService.accept_quote()` PR42 truth gate |
| Capacity interval derivation | `aircraft-capacity-v1` |
| Concurrent overlap arbitration | PostgreSQL PR38 GiST exclusion constraint |
| Booking/capacity release | PR39 atomic termination command |
| Same-operator disruption replacement suitability | PR40 canonical feasibility revalidation |
| Cross-operator replacement | canonical re-procurement |
| Reposition optimizer completeness | PR41 overflow-probe/fail-closed contract |
| Global business mutation authority | canonical application transaction, never graph/optimizer output |

## Failure semantics

Across the chain, fail-closed behavior is intentional:

- missing authoritative position/availability/reference evidence blocks feasibility;
- overlapping active capacity blocks commitment;
- incomplete optimizer universes block a global-plan claim;
- stale or changed aircraft/operator truth blocks award;
- failed award/termination transactions leave no partial authoritative state;
- historical matching, approval, proposal, and decision evidence is not rewritten because later truth
  changes.

## Preserved boundaries

PR38–PR42 deliberately do **not** introduce:

- a second matching policy;
- a second Booking/award authority;
- a temporary disruption capacity-hold state;
- cross-operator replacement outside procurement;
- Quote repricing/substitution during disruption;
- automatic optimizer-to-Booking mutation;
- silent optimizer truncation;
- post-confirmation cancellation policy;
- crew, permit, payment-rail, or refund scheduling.

## Verification map

The repository test suite covers the composed chain through unit, PostgreSQL integration,
concurrency, regression, and evidence tests, including:

- cross-Mission overlapping award races;
- non-overlapping reuse;
- rollback on reservation conflict;
- release and reuse after valid termination;
- competing terminal commands;
- same-operator replacement feasibility failures;
- no hidden replacement capacity hold;
- optimizer boundary success/fail-closed cases;
- award-time aircraft/operator/availability/position revalidation;
- preservation of historical approval evidence on failed award;
- atomic absence of Booking/reservation/award evidence after failed commitment.

For the individual decision details, use ADRs 0027–0031. This assurance document is the integration
map, not a replacement for those ADRs.
