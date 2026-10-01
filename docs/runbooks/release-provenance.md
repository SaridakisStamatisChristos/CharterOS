# CharterOS release-provenance runbook

PR47 introduced the signed release-evidence chain; PR48 extends the required release gate with the
observability smoke. Every repository-owned CI run that reaches the provenance job can therefore bind
the current quality/observability evidence to the signed release package. This is release evidence,
not a claim that the artifact has been deployed to production.

## Release identity

Never approve or deploy a CharterOS artifact using only a mutable tag such as `latest`.

The release package contains these immutable identities:

- source commit SHA and workflow checkout SHA;
- Docker image content ID;
- SHA-256 digest of `charteros-image.tar`;
- SHA-256 digest of `uv.lock`;
- SHA-256 digest of `supply-chain-policy.json`;
- SHA-256 digest of the CycloneDX SBOM;
- GitHub artifact-transfer digest;
- signed attestation IDs/URLs and local Sigstore bundles.

For a future registry deployment, also record the registry manifest digest and deploy
`repository@sha256:...`, never tag-only.

## Evidence packages

The quality job uploads an intermediate artifact named like:

`charteros-release-subjects-<source-sha>-<run-id>`

The signing job verifies that transfer, creates and verifies attestations, then uploads the final:

`charteros-release-evidence-<source-sha>-<run-id>`

The final package includes the deployable image archive, SBOM, vulnerability report, JUnit evidence,
migration head, supply-chain policy, gate markers, attestation bundles, attestation index, and
`release-manifest.json`.

## Required release gates

The manifest builder requires explicit pass evidence for dependency locking, supply-chain policy,
Ruff lint/format, mypy, Bandit, pip-audit, Compose configuration, migration smoke, Alembic drift,
recovery verification, resource cleanup, observability smoke, tests, PostgreSQL restart recovery, reposition benchmark,
application boot, secret scan, hardened-image runtime, container vulnerability scan, and SBOM
generation.

If any gate is skipped or fails, the canonical release manifest cannot be created successfully.

## Verify a downloaded release

From a checkout of the exact source revision, place the final artifact contents under
`release-evidence/`, then run:

```bash
python tools/release_manifest.py verify \
  --manifest release-evidence/release-manifest.json \
  --evidence-dir release-evidence \
  --expected-source-sha <40-character-source-sha>
```

Verify signed build provenance:

```bash
gh attestation verify release-evidence/charteros-image.tar \
  --repo SaridakisStamatisChristos/CharterOS \
  --signer-workflow SaridakisStamatisChristos/CharterOS/.github/workflows/ci.yml \
  --bundle release-evidence/provenance.bundle.json
```

Verify the SBOM relationship:

```bash
gh attestation verify release-evidence/charteros-image.tar \
  --repo SaridakisStamatisChristos/CharterOS \
  --signer-workflow SaridakisStamatisChristos/CharterOS/.github/workflows/ci.yml \
  --predicate-type https://cyclonedx.org/bom \
  --bundle release-evidence/sbom.bundle.json
```

Verify the manifest itself:

```bash
gh attestation verify release-evidence/release-manifest.json \
  --repo SaridakisStamatisChristos/CharterOS \
  --signer-workflow SaridakisStamatisChristos/CharterOS/.github/workflows/ci.yml \
  --bundle release-evidence/release-manifest.bundle.json
```

For release approval, additionally constrain the expected source digest with
`--source-digest <workflow-checkout-sha>` from the manifest.

## Updating the Python base image

1. Resolve the intended official Python image and immutable multi-platform digest.
2. Review upstream release/security information.
3. Update both Dockerfile `FROM` lines and `supply-chain-policy.json`.
4. Do not replace the digest with a tag-only reference.
5. Run the full CI/provenance pipeline and review the new SBOM/vulnerability result.

## Updating dependencies

Change `pyproject.toml` and regenerate `uv.lock` in a normal pull request. Do not bypass review or
merge dependency updates solely because an updater opened them. The same audits, tests, image scans,
SBOM generation, provenance generation, and verification apply.

## Keyless signing boundary

No long-lived private signing key is stored by CharterOS. GitHub Actions obtains a short-lived OIDC
identity for the dedicated provenance job and the attestation action uses Sigstore-backed signing.

Runtime application credentials are not signing credentials. Production deployment identity must
remain separate from release-signing identity.

## What signed CI release evidence does not prove

A green signed CI artifact does not prove that production deployed that exact digest, that rollback
was exercised, or that production SLOs were met. Those require deployment and operational evidence
outside this PR.
