from __future__ import annotations

import json
from pathlib import Path

from tools.check_supply_chain_policy import validate_repository_policy
from tools.release_manifest import (
    REQUIRED_GATES,
    build_release_manifest,
    verify_release_manifest,
)


def test_repository_supply_chain_policy_is_self_consistent() -> None:
    root = Path(__file__).resolve().parents[2]
    assert validate_repository_policy(root) == ()


def test_release_manifest_binds_artifacts_and_detects_tampering(tmp_path: Path) -> None:
    source_sha = "a" * 40
    checkout_sha = "b" * 40
    base_sha = "c" * 40
    repository = "example/charteros"
    evidence = tmp_path / "release-evidence"
    gates = evidence / "gates"
    gates.mkdir(parents=True)

    for gate in REQUIRED_GATES:
        (gates / f"{gate}.passed").touch()

    (evidence / "pytest.xml").write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1">'
        "</testsuite></testsuites>\n",
        encoding="utf-8",
    )
    (evidence / "trivy-vuln.json").write_text(
        json.dumps({"Results": []}),
        encoding="utf-8",
    )
    (evidence / "sbom.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [{"type": "library", "name": "example"}],
            }
        ),
        encoding="utf-8",
    )
    (evidence / "image-inspect.json").write_text(
        json.dumps(
            [
                {
                    "Id": "sha256:" + ("d" * 64),
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.revision": source_sha,
                            "org.opencontainers.image.source": (f"https://github.com/{repository}"),
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (evidence / "migration-head.txt").write_text(
        "0025_commercial_data_governance (head)\n",
        encoding="utf-8",
    )
    (evidence / "charteros-image.tar").write_bytes(b"container-archive")
    provenance_bundle = evidence / "provenance.bundle.json"
    provenance_bundle.write_text('{"bundle":"provenance"}\n', encoding="utf-8")
    sbom_bundle = evidence / "sbom.bundle.json"
    sbom_bundle.write_text('{"bundle":"sbom"}\n', encoding="utf-8")

    manifest = build_release_manifest(
        evidence_dir=evidence,
        source_sha=source_sha,
        checkout_sha=checkout_sha,
        base_sha=base_sha,
        repository=repository,
        event_name="pull_request",
        workflow_ref="example/charteros/.github/workflows/ci.yml@refs/pull/47/merge",
        run_id="47",
        run_number="470",
        run_attempt="1",
        intermediate_artifact_digest="sha256:" + ("e" * 64),
        uv_version="0.12.21",
        trivy_version="0.74.0",
        python_version="3.13.15",
        attest_action_sha="1e69f48acb82d1966a394da916b4c1698aa569d6",
        provenance_attestation_id="1001",
        provenance_attestation_url="https://github.com/example/charteros/attestations/1001",
        provenance_bundle=provenance_bundle,
        sbom_attestation_id="1002",
        sbom_attestation_url="https://github.com/example/charteros/attestations/1002",
        sbom_bundle=sbom_bundle,
    )
    manifest_path = evidence / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert (
        verify_release_manifest(
            manifest_path=manifest_path,
            evidence_dir=evidence,
            expected_source_sha=source_sha,
        )
        == ()
    )

    (evidence / "charteros-image.tar").write_bytes(b"tampered")
    issues = verify_release_manifest(
        manifest_path=manifest_path,
        evidence_dir=evidence,
        expected_source_sha=source_sha,
    )
    assert "container archive digest mismatch" in issues
