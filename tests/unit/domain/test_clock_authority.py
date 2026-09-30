from pathlib import Path

FORBIDDEN_WALL_CLOCK_READS = (
    "datetime.now(",
    "datetime.utcnow(",
    "date.today(",
    "time.time(",
)


def test_domain_layer_does_not_read_system_wall_clock() -> None:
    offenders: list[str] = []
    for path in sorted(Path("charteros/domain").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_WALL_CLOCK_READS:
            if token in source:
                offenders.append(f"{path}: {token}")

    assert offenders == []
