from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime

from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.shared.config import get_settings
from charteros.shared.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="CharterOS Charter Graph projection operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rebuild = subparsers.add_parser(
        "rebuild",
        help="Build or resume a projection version from authoritative outbox history",
    )
    rebuild.add_argument("--target-version", type=int, required=True)
    rebuild.add_argument(
        "--reset-building",
        action="store_true",
        help="Reset only an existing building target before replay",
    )
    rebuild.add_argument(
        "--activate",
        action="store_true",
        help="Activate the verified target after replay",
    )
    rebuild.add_argument(
        "--maintenance-mode",
        action="store_true",
        help="Confirm writes/workers are quiesced for the activation pointer switch",
    )

    verify = subparsers.add_parser("verify", help="Verify a projection against event history")
    verify.add_argument("--version", type=int, required=True)

    activate = subparsers.add_parser(
        "activate",
        help="Activate an already verified projection version",
    )
    activate.add_argument("--version", type=int, required=True)
    activate.add_argument(
        "--maintenance-mode",
        action="store_true",
        help="Confirm writes/workers are quiesced for the activation pointer switch",
    )

    subparsers.add_parser("status", help="List persisted projection versions")

    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings)
    engine = build_engine(settings)
    store = SqlAlchemyGraphProjectionStore(build_session_factory(engine))
    try:
        if args.command == "rebuild":
            report = store.rebuild(
                args.target_version,
                now=datetime.now(UTC),
                reset_building=args.reset_building,
            )
            _print_json(asdict(report))
            if not report.ok:
                raise SystemExit(1)
            if args.activate:
                store.activate(
                    args.target_version,
                    now=datetime.now(UTC),
                    maintenance_mode=args.maintenance_mode,
                )
            return
        if args.command == "verify":
            report = store.verify(args.version)
            _print_json(asdict(report))
            if not report.ok:
                raise SystemExit(1)
            return
        if args.command == "activate":
            store.activate(
                args.version,
                now=datetime.now(UTC),
                maintenance_mode=args.maintenance_mode,
            )
            status = next(
                item for item in store.statuses() if item.projection_version == args.version
            )
            _print_json(asdict(status))
            return
        if args.command == "status":
            _print_json([asdict(item) for item in store.statuses()])
            return
        raise AssertionError(f"unknown command: {args.command}")
    finally:
        engine.dispose()


def _print_json(value: object) -> None:
    print(json.dumps(value, default=str, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
