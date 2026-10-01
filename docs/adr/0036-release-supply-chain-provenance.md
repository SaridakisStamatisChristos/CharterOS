# ADR 0036 — Verifiable release and supply-chain provenance

**Status:** Accepted  
**Date:** 2026-10-01  
**Decision:** PR47

## Context

CharterOS already runs dependency auditing, secret scanning, container vulnerability scanning, and
CycloneDX SBOM generation. Those controls answer whether a build passed selected checks, but they do
not by themselves prove which source commit, dependency lock, SBOM, and deployable artifact belong
together.

PR47 makes release identity digest-based and adds signed provenance and verification around the
existing build rather than introducing another scanner.

## Decision

1. A release candidate is identified by immutable SHA-256 digests. A mutable container tag is never
   sufficient release identity.
2. The Python runtime/base image is pinned by digest in the Dockerfile. Updating that digest is an
   explicit reviewed source change.
3. The repository supply-chain policy is machine checked. Python dependency sources must resolve
   through approved registries/artifact hosts, locked artifacts must carry SHA-256 hashes, editable
   sources are explicitly allow-listed, and external GitHub Actions are pinned to full commit SHAs.
4. The build embeds the source commit and repository URI as OCI labels.
5. The quality job remains read-only. It produces the tested container archive, CycloneDX SBOM,
   lockfile, supply-chain policy, test evidence, migration head, vulnerability report, and explicit
   pass markers for release gates.
6. The quality evidence is transferred to a separate provenance job. Only the provenance job receives
   short-lived OIDC and GitHub attestation write permissions.
7. The provenance job generates standards-compatible GitHub artifact attestations using the official
   `actions/attest` action pinned to a reviewed commit. GitHub's action emits in-toto/SLSA build
   provenance signed through Sigstore.
8. The same deployable container archive receives a CycloneDX SBOM attestation, cryptographically
   binding the software bill of materials to the artifact.
9. Both attestations are verified before the release manifest is created. Verification constrains the
   repository, signer workflow, source digest, predicate type, and subject digest.
10. The canonical `release-manifest.json` records source/build identity, dependency lock and policy
    digests, image/archive digest, SBOM digest, test result/count, migration head, vulnerability
    summary, tool versions, base-image digest, workflow identity, and attestation references.
11. The release manifest is itself separately attested and verified.
12. The release-manifest builder refuses to claim a required gate unless the quality job produced the
    corresponding success marker. A skipped or failed gate therefore cannot become a successful
    release manifest.
13. Dependency updates remain ordinary reviewed source changes. PR47 does not enable unattended
    dependency auto-merge.

## Signing model

CharterOS uses GitHub artifact attestations with keyless, short-lived OIDC identity rather than a
repository-stored long-lived signing key. The signing identity is the dedicated provenance job and
workflow identity, not the running CharterOS application and not a production runtime credential.

The attestation action and artifact transfer actions are pinned to full Git commit SHAs. Their pinned
identities are part of the repository supply-chain policy and must be updated through normal review.

## Artifact model

The current repository does not publish a production registry image. PR47 therefore does not invent a
registry or claim a registry manifest digest that does not exist. The CI release candidate is the exact
Docker archive produced from the hardened image, with:

- a SHA-256 archive digest;
- the Docker image content ID;
- source/repository OCI labels;
- signed SLSA provenance covering the archive, SBOM, lockfile, and supply-chain policy;
- a signed CycloneDX SBOM relationship;
- a signed canonical release manifest.

If a later deployment pipeline pushes the image to a registry, that pipeline must record and attest
the resulting registry manifest digest and deploy by digest rather than tag.

## Base-image policy

The runtime/build base is currently:

`python:3.13.15-slim-bookworm@sha256:3d7f1033ff66b511e51a7c5c3e7907478048b874488b258cd98e50f59a368d67`

A base-image refresh is a deliberate source diff. The reviewed digest is changed in both the Dockerfile
and `supply-chain-policy.json`, then the full CI/provenance pipeline must pass again.

## Dependency policy

`uv.lock` remains canonical for Python dependency resolution. The policy checker rejects unapproved
registry/editable sources and missing SHA-256 artifact hashes. Existing `pip-audit`, Trivy, secret
scan, and CycloneDX generation remain mandatory release gates.

No automation introduced by PR47 can merge dependency changes without normal repository review.

## Rejected alternatives

### Mutable tag as release identity

Rejected because a tag can be moved without changing its human-readable name.

### Long-lived signing key in repository secrets

Rejected because it expands key-management risk and unnecessarily couples signing authority to a
persistent secret.

### Custom signature or provenance format

Rejected because GitHub artifact attestations already provide signed in-toto/SLSA provenance and
Sigstore verification.

### Generate a manifest before checks finish

Rejected because it can create authoritative-looking evidence for checks that never ran.

### Pretend the CI image has a registry digest

Rejected because this repository currently builds locally and does not push a registry image. PR47
records only digests actually observed for produced artifacts.

## Compatibility

PR47 changes release/build evidence only. It does not change business-domain behavior, database
schema, API authorization, recovery semantics, transaction handling, evidence ledgers, or governance
rules.

## Assurance

CI now proves that the repository policy is internally consistent, the full application suite passes,
the hardened container passes runtime and vulnerability checks, release subjects survive artifact
transfer, build and SBOM attestations verify cryptographically, the canonical manifest is internally
consistent, and the manifest itself has a verifiable provenance attestation.
