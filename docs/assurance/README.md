# CharterOS assurance documentation

Assurance documents explain what the repository can prove, what it tests, and where live execution
evidence is still required.

## Core assurance documents

- [Data-governance classification](data-governance-classification.md) — data classes, retention
  posture, exportability, legal-hold applicability, and governance authority.
- [PR31 event ordering / projection assurance](pr31-event-ordering-projection.md)
- [PR32 solver-boundary performance assurance](pr32-solver-boundary-performance.md)
- [PR33 determinism / clock assurance](pr33-determinism-clock.md)
- [PR38–PR42 aircraft commitment hardening](pr38-pr42-aircraft-commitment-hardening.md) — cross-PR
  capacity, release, replacement-feasibility, optimizer-completeness, and award-truth invariant map.
- [Production-readiness index](production-readiness/README.md)

## Evidence interpretation

Assurance material is intentionally conservative:

- code-level tests establish implementation behavior within the tested boundary;
- CI release evidence establishes which source, lockfile, SBOM, image archive, and attestations belong
  together;
- recovery verification establishes restored-state consistency when executed against a restore;
- load/SLO/alert evidence is environment-specific and must not be inferred from code alone.
