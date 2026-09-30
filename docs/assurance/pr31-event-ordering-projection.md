# PR31 — Event-Ordering / Projection Adversarial Assurance

## Scope

Production Hardening PR31 validates the existing transactional-outbox and Charter Graph ordering
contract. It does not introduce a global event sequence, replace PostgreSQL authority, or redesign
projection storage.

The contract under test is:

- delivery is at least once;
- event identity is stable;
- consumer deduplication is durable and transactional;
- projection causality is ordered by
  `aggregate_type + aggregate_id + aggregate_version`;
- `recorded_at` is a checkpoint horizon/tie-break input, not a global commit sequence;
- future aggregate versions fail closed as gaps;
- redelivery after a committed projection mutation is harmless because the receipt is committed
  atomically with the mutation;
- unrelated aggregate streams may interleave independently;
- deterministic rebuild orders authoritative history by aggregate identity/version and converges to
  the same graph digest.

## Adversarial matrix

| Scenario | Expected property | Regression |
| --- | --- | --- |
| Same aggregate, in order | versions advance exactly once | `test_same_aggregate_reversed_gap_then_converges_and_delayed_old_event_is_idempotent` |
| Same aggregate, reversed | future version fails as a gap; predecessor then retry converges | same |
| Aggregate-version gap | no cursor/checkpoint/receipt is silently advanced | same + poison/requeue regression |
| Duplicate event | durable receipt returns duplicate/no-op | delayed-old-event regression |
| Delayed older event | already-receipted old event cannot roll projection state backward | delayed-old-event regression |
| Two workers racing adjacent versions | aggregate lock serializes mutation; gap/retry converges | `test_two_workers_racing_adjacent_versions_converge_without_silent_divergence` |
| Unrelated aggregates interleaved | no fictional global timestamp order is required | `test_unrelated_aggregates_may_interleave_without_global_timestamp_order` |
| Worker crash after claim | expired lease is reclaimed and event projects once | `test_worker_crash_after_claim_reclaims_expired_lease_and_projects_once` |
| Worker crash after projection commit | redelivery observes receipt; checkpoint stays single-counted | `test_worker_crash_after_projection_commit_redelivery_is_receipt_idempotent` |
| Poison event | exhausted gap retry is quarantined | `test_gap_event_can_poison_then_requeue_after_predecessor_and_converge` |
| Explicit requeue | after predecessor lands, poisoned successor can be safely replayed | same |
| Full rebuild from zero | authoritative history rebuild verifies | `test_full_rebuild_from_zero_converges_to_identical_digest` |
| Rebuild digest comparison | independent versions converge to identical deterministic digest | same |

## Architectural conclusion

PR31 intentionally starts from the PR14/PR15 contract instead of assuming
`recorded_at != commit order` is a defect. A new sequence/order mechanism is justified only if
these adversarial tests reproduce projection corruption that the existing causal cursors,
transactional receipts, or rebuild verifier cannot contain.

No schema migration is part of this assurance change.
