from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTION_RE = re.compile(r"^\s*-\s+uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
_FROM_RE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+\S+)?$", re.MULTILINE | re.IGNORECASE)


def validate_repository_policy(root: Path) -> tuple[str, ...]:
    policy_path = root / "supply-chain-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    issues: list[str] = []

    _validate_policy_document(policy, issues)
    _validate_dockerfile(root / "Dockerfile", policy, issues)
    _validate_uv_lock(root / "uv.lock", policy, issues)
    _validate_actions(root / ".github" / "workflows" / "ci.yml", policy, issues)
    return tuple(issues)


def _validate_policy_document(policy: dict[str, object], issues: list[str]) -> None:
    if policy.get("schema_version") != "charteros.supply-chain-policy.v1":
        issues.append("unexpected supply-chain policy schema version")

    base = _mapping(policy, "python_base_image", issues)
    digest = base.get("digest")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        issues.append("python base-image digest must be an immutable sha256 digest")

    release_identity = _mapping(policy, "release_identity", issues)
    if release_identity.get("digest_required") is not True:
        issues.append("release identity must require a digest")
    if release_identity.get("mutable_tag_sufficient") is not False:
        issues.append("mutable tags must never be sufficient release identity")

    review_policy = _mapping(policy, "review_policy", issues)
    if review_policy.get("dependency_updates_require_review") is not True:
        issues.append("dependency updates must require review")
    if review_policy.get("automatic_dependency_merge_allowed") is not False:
        issues.append("automatic dependency merge must be disabled by policy")


def _validate_dockerfile(
    path: Path,
    policy: dict[str, object],
    issues: list[str],
) -> None:
    document = path.read_text(encoding="utf-8")
    base = _mapping(policy, "python_base_image", issues)
    reference = base.get("reference")
    digest = base.get("digest")
    if not isinstance(reference, str) or not isinstance(digest, str):
        return

    expected = f"{reference}@{digest}"
    images = _FROM_RE.findall(document)
    if not images:
        issues.append("Dockerfile has no FROM instruction")
        return
    for image in images:
        if image != expected:
            issues.append(
                f"Dockerfile base image must be exactly {expected!r}; found {image!r}"
            )


def _validate_uv_lock(
    path: Path,
    policy: dict[str, object],
    issues: list[str],
) -> None:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    dependencies = _mapping(policy, "python_dependencies", issues)
    allowed_registries = set(_string_list(dependencies, "allowed_registries", issues))
    allowed_hosts = set(_string_list(dependencies, "allowed_artifact_hosts", issues))
    allowed_editable = set(_string_list(dependencies, "allowed_editable_packages", issues))
    require_hashes = dependencies.get("require_sha256_artifact_hashes") is True

    packages = document.get("package")
    if not isinstance(packages, list):
        issues.append("uv.lock does not contain a package list")
        return

    for package in packages:
        if not isinstance(package, dict):
            issues.append("uv.lock contains a malformed package entry")
            continue
        name = package.get("name")
        source = package.get("source")
        if not isinstance(name, str) or not isinstance(source, dict):
            issues.append("uv.lock package is missing name/source")
            continue

        if "editable" in source:
            if name not in allowed_editable or source.get("editable") != ".":
                issues.append(f"unapproved editable dependency source for {name}")
            continue

        registry = source.get("registry")
        if not isinstance(registry, str) or registry not in allowed_registries:
            issues.append(f"unapproved dependency source for {name}: {source!r}")
            continue

        artifacts: list[dict[str, object]] = []
        sdist = package.get("sdist")
        if isinstance(sdist, dict):
            artifacts.append(sdist)
        wheels = package.get("wheels")
        if isinstance(wheels, list):
            artifacts.extend(item for item in wheels if isinstance(item, dict))
        if not artifacts:
            issues.append(f"registry dependency {name} has no locked artifacts")
            continue

        for artifact in artifacts:
            url = artifact.get("url")
            digest = artifact.get("hash")
            if not isinstance(url, str):
                issues.append(f"locked artifact for {name} has no URL")
            else:
                host = urlparse(url).hostname
                if host not in allowed_hosts:
                    issues.append(f"unapproved artifact host for {name}: {host!r}")
            if require_hashes and (
                not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None
            ):
                issues.append(f"locked artifact for {name} lacks a sha256 hash")


def _validate_actions(
    path: Path,
    policy: dict[str, object],
    issues: list[str],
) -> None:
    document = path.read_text(encoding="utf-8")
    actions = _mapping(policy, "github_actions", issues)
    require_sha = actions.get("require_full_commit_sha") is True

    discovered = _ACTION_RE.findall(document)
    for action, ref in discovered:
        if action.startswith("./"):
            continue
        if require_sha and re.fullmatch(r"[0-9a-f]{40}", ref) is None:
            issues.append(f"GitHub Action {action}@{ref} is not pinned to a full commit SHA")

    expected = {
        "actions/attest": actions.get("attest_sha"),
        "actions/upload-artifact": actions.get("upload_artifact_sha"),
        "actions/download-artifact": actions.get("download_artifact_sha"),
    }
    by_action: dict[str, set[str]] = {}
    for action, ref in discovered:
        by_action.setdefault(action, set()).add(ref)

    for action, expected_sha in expected.items():
        if not isinstance(expected_sha, str):
            issues.append(f"policy is missing pinned SHA for {action}")
            continue
        refs = by_action.get(action)
        if refs is None:
            issues.append(f"CI does not use required supply-chain action {action}")
        elif refs != {expected_sha}:
            issues.append(
                f"{action} must use policy SHA {expected_sha}; found {sorted(refs)}"
            )


def _mapping(
    document: dict[str, object],
    key: str,
    issues: list[str],
) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        issues.append(f"policy field {key!r} must be an object")
        return {}
    return value


def _string_list(
    document: dict[str, object],
    key: str,
    issues: list[str],
) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        issues.append(f"policy field {key!r} must be a string list")
        return ()
    return tuple(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify CharterOS release supply-chain policy."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    issues = validate_repository_policy(args.root.resolve())
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        raise SystemExit(1)
    print("Supply-chain policy verified.")


if __name__ == "__main__":
    main()
