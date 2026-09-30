# PR33 — Determinism, Clock, and Code-Quality Assurance

## Scope

Production Hardening PR33 closes the repository-hardening roadmap with two explicit deterministic
boundaries and a narrow production-quality sweep:

1. authoritative application time; and
2. geographic distance at business eligibility boundaries.

It does not claim live deployment certification. PostgreSQL remains canonical transactional truth,
Charter Graph remains derived, and prior FX, Tender, Booking, outbox, evidence, and solver
authorities remain unchanged.

## Authoritative clock

CharterOS now defines a small clock port:

- `Clock` — protocol consumed by the application/API boundary;
- `SystemClock` — production implementation returning aware UTC system time;
- `FrozenClock` — deterministic test implementation that normalizes an explicit aware instant to
  UTC and rejects naive datetimes.

The FastAPI composition root installs the clock on application state. `get_clock` exposes it as a
dependency. Route modules receive a typed `ClockDep` and call `clock.now()` instead of reading the
system wall clock directly.

Internal quote and tender helpers receive `Clock` explicitly from their route callers. They do not
resolve dependencies or read global time themselves.

A source-level regression test scans every `apps/api/routes/*.py` module and fails if direct
`datetime.now(...)` or `datetime.utcnow(...)` calls return.

This preserves the existing application-service contracts that accept explicit `now` /
`evaluated_at` / `recorded_at` values while removing request-layer system-time authority.

## Geographic distance determinism

The matching and repositioning domains continue to use Haversine. PR33 does not replace
transcendental geometry with fake Decimal trigonometry.

The deterministic policy is:

```text
canonical Decimal coordinates
        ↓ ROUND_HALF_UP to 0.000001 degree
float/libm Haversine
        ↓ ROUND_HALF_UP to 0.000001 NM stability quantum
business distance
        ↓ ROUND_HALF_UP to 0.1 NM
integer tenths of NM
```

The coordinate quantum is much smaller than the business-visible 0.1-NM unit. The intermediate
1e-6-NM stabilization quantum absorbs insignificant libm/platform noise before the eligibility
boundary is rounded.

The same centralized function remains the source for:

- route range calculations;
- matching reposition distance;
- PR18 baseline/pre/post reposition distance.

No separate tolerance path can admit a supplier that the canonical integer distance rejects.

## Boundary regressions

PR33 adds tests for:

- production clock returns aware UTC;
- frozen clock normalizes explicit offsets;
- frozen clock rejects naive datetimes;
- route source cannot directly read the system wall clock;
- distance policy quanta are pinned;
- Haversine symmetry;
- zero distance;
- a known ATH/JFK coordinate pair;
- sub-quantum coordinate noise cannot alter the business distance;
- libm output stabilization;
- exact half-up 0.1-NM boundary behavior.

## Remaining code-quality discipline

PR33 intentionally avoids a generic cleanup rewrite. The broad current quality gate remains the
evidence for production-code hygiene:

- Ruff lint and format;
- strict mypy;
- Bandit;
- frozen dependency vulnerability audit;
- PostgreSQL migration smoke;
- `alembic check`;
- full PostgreSQL pytest;
- API boot smoke;
- repository secret scan;
- hardened image build/runtime smoke;
- HIGH/CRITICAL container vulnerability scan;
- CycloneDX SBOM generation and validation.

PR30 already removed demonstrated production-assert findings. PR33 does not reopen completed
findings unless the live branch reproduces a defect.

## Release boundary

A green PR33 completes the repository hardening roadmap, not commercial deployment certification.
Real identity provider integration, managed PostgreSQL operation, PITR/restore evidence,
authenticated end-to-end deployment smoke, real capacity/SLO measurement, alert delivery,
network/TLS validation, and other environment-specific evidence remain a separate release gate.
