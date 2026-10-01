from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_GATES: tuple[str, ...] = (
    "dependency-lock",
    "supply-chain-policy",
    "ruff-lint",
    "ruff-format",
    "mypy",
    "bandit",
    "pip-audit",
    "compose-config",
    "migration-smoke",
    "alembic-drift",
    "recovery-verification",
    "resource-cleanup",
    "tests",
    "postgres-restart",
    "reposition-benchmark",
    "app-boot",
    "secret-scan",
    "image-runtime",
    "container-vulnerability-scan",
    "sbom",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_manifest(
    *,
    evidence_dir: Path,
    source_sha: str,
    checkout_sha: str,
    base_sha: str | None,
    repository: str,
    event_name: str,
    workflow_ref: str,
    run_id: str,
    run_number: str,
    run_attempt: str,
    intermediate_artifact_digest: str,
    uv_version: str,
    trivy_version: str,
    python_version: str,
    attest_action_sha: str,
    provenance_attestation_id: str,
    provenance_attestation_url: str,
    provenance_bundle: Path,
    sbom_attestation_id: str,
    sbom_attestation_url: str,
    sbom_bundle: Path,
) -> dict[str, Any]:
    _require_sha(source_sha, "source SHA")
    _require_sha(checkout_sha, "checkout SHA")
    if base_sha:
        _require_sha(base_sha, "base SHA")
    if not _SHA256_RE.fullmatch(intermediate_artifact_digest):
        raise ValueError("intermediate artifact digest must be sha256:<64 hex>")

    policy_path = Path("supply-chain-policy.json")
    policy = _read_json_object(policy_path)
    base_image = _require_mapping(policy, "python_base_image")
    base_reference = _require_string(base_image, "reference")
    base_digest = _require_string(base_image, "digest")
    if not _SHA256_RE.fullmatch(base_digest):
        raise ValueError("policy base-image digest is not sha256")

    gates = _read_gate_evidence(evidence_dir / "gates")
    junit = _junit_summary(evidence_dir / "pytest.xml")
    if junit["failures"] or junit["errors"] or junit["tests"] < 1:
        raise ValueError(f"test evidence is not passing: {junit!r}")

    trivy = _trivy_summary(evidence_dir / "trivy-vuln.json")
    if trivy["HIGH"] or trivy["CRITICAL"]:
        raise ValueError(f"high/critical vulnerabilities remain: {trivy!r}")

    sbom_path = evidence_dir / "sbom.cdx.json"
    sbom = _read_json_object(sbom_path)
    if sbom.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM is not CycloneDX")
    components = sbom.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("CycloneDX SBOM is empty")

    image_inspect = _read_json_value(evidence_dir / "image-inspect.json")
    if not isinstance(image_inspect, list) or len(image_inspect) != 1:
        raise ValueError("image-inspect.json must contain exactly one image")
    image = image_inspect[0]
    if not isinstance(image, dict):
        raise ValueError("image inspection entry must be an object")
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not _SHA256_RE.fullmatch(image_id):
        raise ValueError("container image ID is missing or not sha256")

    config = image.get("Config")
    if not isinstance(config, dict):
        raise ValueError("container image Config is missing")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise ValueError("container image source labels are missing")
    if labels.get("org.opencontainers.image.revision") != source_sha:
        raise ValueError("container image revision label does not match source SHA")
    expected_source = f"https://github.com/{repository}"
    if labels.get("org.opencontainers.image.source") != expected_source:
        raise ValueError("container image source label does not match repository")

    migration_lines = [
        line.strip()
        for line in (evidence_dir / "migration-head.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(migration_lines) != 1 or "(head)" not in migration_lines[0]:
        raise ValueError(f"expected exactly one Alembic head, got {migration_lines!r}")

    image_tar = evidence_dir / "charteros-image.tar"
    lockfile = Path("uv.lock")
    if not image_tar.is_file():
        raise ValueError("deployable container archive is missing")
    if not lockfile.is_file():
        raise ValueError("uv.lock is missing")
    if not provenance_bundle.is_file() or not sbom_bundle.is_file():
        raise ValueError("attestation bundle is missing")

    _require_attestation_reference(
        provenance_attestation_id,
        provenance_attestation_url,
        "build provenance",
    )
    _require_attestation_reference(
        sbom_attestation_id,
        sbom_attestation_url,
        "SBOM",
    )

    return {
        "schema_version": "charteros.release-manifest.v1",
        "artifact": {
            "name": "charteros-container-archive",
            "image_id": image_id,
            "archive": {
                "path": image_tar.name,
                "sha256": sha256_file(image_tar),
            },
            "identity": {
                "digest_required": True,
                "mutable_tag_sufficient": False,
            },
        },
        "source": {
            "repository": repository,
            "source_sha": source_sha,
            "checkout_sha": checkout_sha,
            "base_sha": base_sha,
            "event_name": event_name,
        },
        "workflow": {
            "workflow_ref": workflow_ref,
            "run_id": run_id,
            "run_number": run_number,
            "run_attempt": run_attempt,
            "intermediate_artifact_digest": intermediate_artifact_digest,
        },
        "dependencies": {
            "lockfile": lockfile.name,
            "lockfile_sha256": sha256_file(lockfile),
            "policy": policy_path.name,
            "policy_sha256": sha256_file(policy_path),
        },
        "base_image": {
            "reference": base_reference,
            "digest": base_digest,
        },
        "sbom": {
            "format": "CycloneDX",
            "path": sbom_path.name,
            "sha256": sha256_file(sbom_path),
            "component_count": len(components),
        },
        "validation": {
            "gates": gates,
            "tests": {
                "status": "passed",
                **junit,
            },
            "container_vulnerability_scan": {
                "status": "passed",
                "severity_counts": trivy,
            },
            "migration_head": migration_lines[0],
        },
        "tools": {
            "python": python_version,
            "uv": uv_version,
            "trivy": trivy_version,
            "actions_attest_commit": attest_action_sha,
        },
        "provenance": {
            "provider": "github-artifact-attestations",
            "build": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "attestation_id": provenance_attestation_id,
                "attestation_url": provenance_attestation_url,
                "bundle_sha256": sha256_file(provenance_bundle),
            },
            "sbom": {
                "predicate_type": "https://cyclonedx.org/bom",
                "attestation_id": sbom_attestation_id,
                "attestation_url": sbom_attestation_url,
                "bundle_sha256": sha256_file(sbom_bundle),
            },
        },
    }


def verify_release_manifest(
    *,
    manifest_path: Path,
    evidence_dir: Path,
    expected_source_sha: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    manifest = _read_json_object(manifest_path)

    if manifest.get("schema_version") != "charteros.release-manifest.v1":
        issues.append("unexpected release manifest schema version")

    source = _safe_mapping(manifest, "source", issues)
    if source.get("source_sha") != expected_source_sha:
        issues.append("release manifest source SHA does not match expected source")

    artifact = _safe_mapping(manifest, "artifact", issues)
    identity = _safe_mapping(artifact, "identity", issues, prefix="artifact")
    if identity.get("digest_required") is not True:
        issues.append("release identity does not require a digest")
    if identity.get("mutable_tag_sufficient") is not False:
        issues.append("release identity incorrectly permits mutable tags")

    archive = _safe_mapping(artifact, "archive", issues, prefix="artifact")
    _verify_digest(
        evidence_dir / "charteros-image.tar",
        archive.get("sha256"),
        "container archive",
        issues,
    )

    dependencies = _safe_mapping(manifest, "dependencies", issues)
    _verify_digest(Path("uv.lock"), dependencies.get("lockfile_sha256"), "uv.lock", issues)
    _verify_digest(
        Path("supply-chain-policy.json"),
        dependencies.get("policy_sha256"),
        "supply-chain policy",
        issues,
    )

    sbom = _safe_mapping(manifest, "sbom", issues)
    _verify_digest(
        evidence_dir / "sbom.cdx.json",
        sbom.get("sha256"),
        "SBOM",
        issues,
    )

    validation = _safe_mapping(manifest, "validation", issues)
    gates = _safe_mapping(validation, "gates", issues, prefix="validation")
    for gate in REQUIRED_GATES:
        if gates.get(gate) != "passed":
            issues.append(f"release gate {gate!r} is not recorded as passed")

    tests = _safe_mapping(validation, "tests", issues, prefix="validation")
    if (
        tests.get("status") != "passed"
        or tests.get("failures") != 0
        or tests.get("errors") != 0
    ):
        issues.append("release manifest test evidence is not passing")

    vulnerability = _safe_mapping(
        validation,
        "container_vulnerability_scan",
        issues,
        prefix="validation",
    )
    severities = _safe_mapping(
        vulnerability,
        "severity_counts",
        issues,
        prefix="validation.container_vulnerability_scan",
    )
    if vulnerability.get("status") != "passed":
        issues.append("container vulnerability scan is not recorded as passed")
    if severities.get("HIGH") != 0 or severities.get("CRITICAL") != 0:
        issues.append("release manifest records high/critical vulnerabilities")

    provenance = _safe_mapping(manifest, "provenance", issues)
    for name, bundle_name in (
        ("build", "provenance.bundle.json"),
        ("sbom", "sbom.bundle.json"),
    ):
        attestation = _safe_mapping(provenance, name, issues, prefix="provenance")
        url = attestation.get("attestation_url")
        if not isinstance(url, str) or not url.startswith("https://github.com/"):
            issues.append(f"{name} attestation URL is missing or invalid")
        _verify_digest(
            evidence_dir / bundle_name,
            attestation.get("bundle_sha256"),
            f"{name} attestation bundle",
            issues,
        )

    return tuple(issues)


def _read_gate_evidence(gate_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    missing: list[str] = []
    for gate in REQUIRED_GATES:
        marker = gate_dir / f"{gate}.passed"
        if marker.is_file():
            result[gate] = "passed"
        else:
            missing.append(gate)
    if missing:
        raise ValueError(f"release evidence is missing gate markers: {missing!r}")
    return result


def _junit_summary(path: Path) -> dict[str, int]:
    document = path.read_text(encoding="utf-8")
    match = re.search(r"<testsuite\\b([^>]*)>", document)
    if match is None:
        raise ValueError("JUnit evidence does not contain a testsuite")
    attributes = match.group(1)
    totals: dict[str, int] = {}
    for key in ("tests", "failures", "errors", "skipped"):
        value = re.search(rf'\\b{key}="(\\d+)"', attributes)
        if value is None:
            raise ValueError(f"JUnit testsuite is missing {key!r}")
        totals[key] = int(value.group(1))
    return totals


def _trivy_summary(path: Path) -> dict[str, int]:
    document = _read_json_object(path)
    counts = {"UNKNOWN": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    results = document.get("Results", [])
    if not isinstance(results, list):
        raise ValueError("Trivy report Results must be a list")
    for result in results:
        if not isinstance(result, dict):
            continue
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValueError("Trivy Vulnerabilities must be a list")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            severity = str(vulnerability.get("Severity", "UNKNOWN")).upper()
            counts[severity if severity in counts else "UNKNOWN"] += 1
    return counts


def _require_attestation_reference(attestation_id: str, url: str, label: str) -> None:
    if not attestation_id.strip():
        raise ValueError(f"{label} attestation ID is missing")
    if not url.startswith("https://github.com/"):
        raise ValueError(f"{label} attestation URL is invalid")


def _require_sha(value: str, label: str) -> None:
    if _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a 40-character hexadecimal Git SHA")


def _verify_digest(
    path: Path,
    expected: object,
    label: str,
    issues: list[str],
) -> None:
    if not path.is_file():
        issues.append(f"{label} is missing")
        return
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        issues.append(f"{label} digest is missing or malformed")
        return
    actual = sha256_file(path)
    if actual != expected:
        issues.append(f"{label} digest mismatch")


def _read_json_object(path: Path) -> dict[str, Any]:
    value = _read_json_value(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_json_value(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_mapping(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"field {key!r} must be an object")
    return value


def _require_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"field {key!r} must be a non-empty string")
    return value


def _safe_mapping(
    document: dict[str, Any],
    key: str,
    issues: list[str],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        path = f"{prefix}.{key}" if prefix else key
        issues.append(f"manifest field {path!r} must be an object")
        return {}
    return value


def _build_command(args: argparse.Namespace) -> None:
    manifest = build_release_manifest(
        evidence_dir=args.evidence_dir,
        source_sha=args.source_sha,
        checkout_sha=args.checkout_sha,
        base_sha=args.base_sha or None,
        repository=args.repository,
        event_name=args.event_name,
        workflow_ref=args.workflow_ref,
        run_id=args.run_id,
        run_number=args.run_number,
        run_attempt=args.run_attempt,
        intermediate_artifact_digest=args.intermediate_artifact_digest,
        uv_version=args.uv_version,
        trivy_version=args.trivy_version,
        python_version=args.python_version,
        attest_action_sha=args.attest_action_sha,
        provenance_attestation_id=args.provenance_attestation_id,
        provenance_attestation_url=args.provenance_attestation_url,
        provenance_bundle=args.provenance_bundle,
        sbom_attestation_id=args.sbom_attestation_id,
        sbom_attestation_url=args.sbom_attestation_url,
        sbom_bundle=args.sbom_bundle,
    )
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_command(args: argparse.Namespace) -> None:
    issues = verify_release_manifest(
        manifest_path=args.manifest,
        evidence_dir=args.evidence_dir,
        expected_source_sha=args.expected_source_sha,
    )
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        raise SystemExit(1)
    print("Release manifest verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and verify CharterOS release evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--evidence-dir", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--source-sha", required=True)
    build.add_argument("--checkout-sha", required=True)
    build.add_argument("--base-sha", default="")
    build.add_argument("--repository", required=True)
    build.add_argument("--event-name", required=True)
    build.add_argument("--workflow-ref", required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--run-number", required=True)
    build.add_argument("--run-attempt", required=True)
    build.add_argument("--intermediate-artifact-digest", required=True)
    build.add_argument("--uv-version", required=True)
    build.add_argument("--trivy-version", required=True)
    build.add_argument("--python-version", required=True)
    build.add_argument("--attest-action-sha", required=True)
    build.add_argument("--provenance-attestation-id", required=True)
    build.add_argument("--provenance-attestation-url", required=True)
    build.add_argument("--provenance-bundle", type=Path, required=True)
    build.add_argument("--sbom-attestation-id", required=True)
    build.add_argument("--sbom-attestation-url", required=True)
    build.add_argument("--sbom-bundle", type=Path, required=True)
    build.set_defaults(handler=_build_command)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--evidence-dir", type=Path, required=True)
    verify.add_argument("--expected-source-sha", required=True)
    verify.set_defaults(handler=_verify_command)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
