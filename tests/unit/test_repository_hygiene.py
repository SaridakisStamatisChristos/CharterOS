from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

_PATH_PATTERNS = (
    re.compile(r"(^|/)pr\d+(?:[-_]|$)", re.IGNORECASE),
    re.compile(r"(^|/)r7-", re.IGNORECASE),
    re.compile(r"handoff", re.IGNORECASE),
    re.compile(r"milestone", re.IGNORECASE),
)

_PRODUCT_TEXT_PATTERNS = (
    re.compile(r"\bPR\d+\b", re.IGNORECASE),
    re.compile(r"\br7-", re.IGNORECASE),
    re.compile(r"\bhandoff\b", re.IGNORECASE),
    re.compile(r"\bmilestone\b", re.IGNORECASE),
    re.compile(r"\bbuild[- ]session\b", re.IGNORECASE),
)

_TEST_IDENTIFIER_PATTERN = re.compile(r"\b(?:def\s+)?test_pr\d+", re.IGNORECASE)


def _active_paths() -> list[Path]:
    paths: list[Path] = []
    for root in (ROOT / "apps", ROOT / "charteros", ROOT / "tests", ROOT / "tools"):
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    for root in (ROOT / "docs", ROOT / ".github"):
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    for name in ("README.md", "EVALUATION.md", "Makefile", "pyproject.toml"):
        path = ROOT / name
        if path.exists():
            paths.append(path)
    return paths


def test_active_tree_uses_evergreen_product_naming() -> None:
    offenders: list[str] = []
    for path in _active_paths():
        if path.resolve() == THIS_FILE:
            continue
        relative = path.relative_to(ROOT).as_posix()
        # ADR numbers and migration revision identities are durable technical history, not
        # product-development chronology, and are intentionally outside this naming check.
        if relative.startswith("docs/adr/") or relative.startswith("migrations/versions/"):
            continue
        if any(pattern.search(relative) for pattern in _PATH_PATTERNS):
            offenders.append(f"path:{relative}")
    assert not offenders, "development-era path names remain:\n" + "\n".join(offenders)


def test_product_facing_text_avoids_development_chronology() -> None:
    offenders: list[str] = []
    scan_roots = [ROOT / "README.md", ROOT / "EVALUATION.md", ROOT / "docs", ROOT / ".github"]
    files: list[Path] = []
    for root in scan_roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())

    for path in files:
        if path.resolve() == THIS_FILE:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("docs/adr/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _PRODUCT_TEXT_PATTERNS):
                offenders.append(f"{relative}:{line_number}:{line.strip()}")
    assert not offenders, "development chronology remains in product-facing text:\n" + "\n".join(
        offenders
    )


def test_test_identifiers_are_capability_named() -> None:
    offenders: list[str] = []
    for path in (ROOT / "tests").rglob("*.py"):
        if path.resolve() == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _TEST_IDENTIFIER_PATTERN.search(line):
                relative = path.relative_to(ROOT).as_posix()
                offenders.append(f"{relative}:{line_number}:{line.strip()}")
    assert not offenders, "development-era test identifiers remain:\n" + "\n".join(offenders)
