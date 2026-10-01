# Commercial data-governance runbook

This runbook covers the PR45 governance control plane for buyer and operator tenants. It is an
engineering control runbook, not a legal determination or a claim of regulatory certification.

Design authority: [ADR 0034](../adr/0034-commercial-data-governance.md).  
Classification authority: [data-governance classification](../assurance/data-governance-classification.md).

## Safety invariants

- Existing restrictive foreign keys remain part of the durability boundary; do not replace them with
  blanket `CASCADE` to simplify erasure.
- Evidence-backed and append-only history is not rewritten to simulate deletion.
- Legal hold blocks incompatible lifecycle operations.
- Closure and erasure are distinct operations.
- Tenant export verifies evidence integrity and executes from a read-only repeatable-read snapshot.
- Auth/security internals, application logs, delivery internals, integrity-ledger internals,
  idempotency records, and rate-limit windows are excluded from tenant export by policy.
- Governance actor identity is stored as a SHA-256 subject digest rather than raw principal subject.

## Inspect the active policy

The policy endpoint is administrator-only:

```text
GET /v1/governance/policy
```

It reports the current governance policy version, data-asset policies, dependency graph, exportability,
and legal-hold applicability.

Review the policy before a lifecycle action. Do not assume table behavior from an older deployment.

## Legal hold

Create a legal hold before a lifecycle operation when retention is required:

```text
POST /v1/governance/tenants/{tenant_kind}/{tenant_id}/legal-holds
{"reason": "bounded operator-entered reason"}
```

List holds:

```text
GET /v1/governance/tenants/{tenant_kind}/{tenant_id}/legal-holds
```

Release a hold only with an explicit reason:

```text
POST /v1/governance/legal-holds/{hold_id}/release
{"reason": "bounded release reason"}
```

Legal-hold creation/release and tenant lifecycle events remain auditable governance evidence.

## Close a tenant

Closure deactivates the buyer organization or operator and records the lifecycle result without
pretending historical commercial/evidence state never existed.

```text
POST /v1/governance/tenants/{tenant_kind}/{tenant_id}/close
Idempotency-Key: <operator-controlled unique key>
```

Use a stable idempotency key for retries of the same requested closure. Review the returned lifecycle
report and policy version.

## Erase a tenant

Erasure is dependency-aware and fail-closed:

```text
POST /v1/governance/tenants/{tenant_kind}/{tenant_id}/erase
Idempotency-Key: <operator-controlled unique key>
```

The service first evaluates the explicit dependency graph and blocking evidence/legal-hold state. A
blocked result is not an instruction to bypass the dependency graph manually.

Do not delete rows directly to force a successful report. Preserve the returned lifecycle evidence
and investigate the blocking dependency or hold.

## Export tenant data

Authorized buyer/operator parties may export only their permitted tenant path; administrators have
the corresponding tenant authority.

```text
GET /v1/governance/tenants/{tenant_kind}/{tenant_id}/export?max_missions=100&event_limit=250
```

Bounds:

- `max_missions`: 1..100;
- `event_limit`: 1..500.

Export executes under `REPEATABLE READ, READ ONLY`, verifies evidence integrity before export, and
returns:

- schema version;
- governance policy version;
- tenant kind and ID;
- generation time;
- source provenance;
- bounded master data;
- bounded mission evidence;
- explicitly excluded surfaces;
- deterministic verification digest.

If the export would be incomplete at the requested mission/event bounds, CharterOS fails closed
rather than silently returning a partial document.

## Retention and cleanup boundary

Automated cleanup currently deletes only policy-approved transient/rebuildable state, such as expired
idempotency replay records and API rate-window rows through the bounded resource-cleanup process.
It does not provide a generic command that deletes canonical commercial/evidence history.

Run transient cleanup with:

```bash
make resource-cleanup
# or
python -m apps.resource_cleanup.main
```

Treat changes to retention policy, exportability, legal-hold applicability, or lifecycle dependency
ordering as reviewed source changes with full CI.
