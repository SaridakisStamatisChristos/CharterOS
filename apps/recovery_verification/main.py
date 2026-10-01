from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from charteros.application.recovery import (
    GraphRecoveryState,
    RecoveryManifest,
    elapsed_seconds,
    observed_data_loss_seconds,
    utc,
)
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.repositories.graph import SqlAlchemyGraphProjectionStore
from charteros.infrastructure.db.repositories.recovery import (
    SqlAlchemyRecoveryVerificationRepository,
)
from charteros.shared.config import get_settings
from charteros.shared.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a restored CharterOS PostgreSQL database without rewriting canonical history."
        )
    )
    parser.add_argument(
        "--database-id",
        required=True,
        help="Non-secret operator identifier for the restored database/environment.",
    )
    parser.add_argument("--backup-cutoff-at", type=_parse_timestamp)
    parser.add_argument("--expected-latest-canonical-at", type=_parse_timestamp)
    parser.add_argument(
        "--recovery-started-at",
        type=_parse_timestamp,
        help=(
            "Observed start of the restore drill. If omitted, RTO is explicitly scoped "
            "to verification only."
        ),
    )
    parser.add_argument(
        "--graph-version",
        type=int,
        help="Projection version to verify instead of the active projection.",
    )
    parser.add_argument(
        "--rebuild-graph-version",
        type=int,
        help=(
            "Rebuild this derived Charter Graph version from canonical outbox history "
            "before verification."
        ),
    )
    parser.add_argument(
        "--maintenance-mode",
        action="store_true",
        help="Required acknowledgement when rebuilding derived state.",
    )
    parser.add_argument(
        "--future-tolerance-seconds",
        type=int,
        default=300,
        help="Allowed future timestamp skew before restore verification fails.",
    )
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    if args.graph_version is not None and args.graph_version < 1:
        parser.error("--graph-version must be positive")
    if args.rebuild_graph_version is not None and args.rebuild_graph_version < 1:
        parser.error("--rebuild-graph-version must be positive")
    if args.future_tolerance_seconds < 0:
        parser.error("--future-tolerance-seconds cannot be negative")
    if args.rebuild_graph_version is not None and not args.maintenance_mode:
        parser.error("--rebuild-graph-version requires --maintenance-mode")
    if (
        args.graph_version is not None
        and args.rebuild_graph_version is not None
        and args.graph_version != args.rebuild_graph_version
    ):
        parser.error("--graph-version must match --rebuild-graph-version when both are supplied")

    settings = get_settings()
    configure_logging(settings)
    expected_schema_revision = _expected_alembic_head()
    verification_started_at = datetime.now(UTC)
    recovery_started_at = args.recovery_started_at or verification_started_at
    rto_scope = "restore_and_verification" if args.recovery_started_at else "verification_only"

    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        graph_store = SqlAlchemyGraphProjectionStore(factory)
        rebuild_version: int | None = args.rebuild_graph_version
        graph_report = None
        if rebuild_version is not None:
            graph_report = graph_store.rebuild(
                rebuild_version,
                now=verification_started_at,
                reset_building=True,
            )

        with factory() as session:
            snapshot = SqlAlchemyRecoveryVerificationRepository(session).snapshot(
                verification_time=verification_started_at,
                future_tolerance_seconds=args.future_tolerance_seconds,
            )

        graph_version = rebuild_version or args.graph_version or graph_store.active_version()
        if graph_report is None and graph_version is not None:
            graph_report = graph_store.verify(graph_version)

        if graph_report is None:
            graph_state = GraphRecoveryState(
                projection_version=None,
                rebuild_version=None,
                ok=snapshot.projected_event_count == 0,
                event_count=0,
                reference_digest=None,
                persisted_digest=None,
                issues=(
                    ()
                    if snapshot.projected_event_count == 0
                    else ("projected canonical events exist but no graph projection is available",)
                ),
            )
        else:
            graph_state = GraphRecoveryState(
                projection_version=graph_report.projection_version,
                rebuild_version=rebuild_version,
                ok=graph_report.ok,
                event_count=graph_report.event_count,
                reference_digest=graph_report.reference_digest,
                persisted_digest=graph_report.persisted_digest,
                issues=graph_report.issues,
            )

        completed_at = datetime.now(UTC)
        data_loss_seconds = observed_data_loss_seconds(
            expected_latest_canonical_at=args.expected_latest_canonical_at,
            restored_latest_canonical_at=snapshot.latest_canonical_event_at,
        )
        issues = _issues(
            expected_schema_revision=expected_schema_revision,
            restored_schema_revision=snapshot.schema_revision,
            graph=graph_state,
            evidence_violation_count=snapshot.evidence_violation_count,
            poisoned_event_count=snapshot.outbox.poisoned_event_count,
            capacity_overlap_count=snapshot.capacity_overlap_count,
            orphan_count=sum(item.orphan_count for item in snapshot.orphan_references),
            future_timestamp_count=snapshot.future_timestamp_count,
            expected_latest_canonical_at=args.expected_latest_canonical_at,
            restored_latest_canonical_at=snapshot.latest_canonical_event_at,
        )
        manifest = RecoveryManifest(
            schema_version="charteros.recovery-manifest.v1",
            database_id=args.database_id,
            environment=settings.environment,
            expected_schema_revision=expected_schema_revision,
            restored_schema_revision=snapshot.schema_revision,
            schema_revision_ok=snapshot.schema_revision == expected_schema_revision,
            backup_cutoff_at=args.backup_cutoff_at,
            latest_canonical_event_at=snapshot.latest_canonical_event_at,
            expected_latest_canonical_at=args.expected_latest_canonical_at,
            graph=graph_state,
            evidence_integrity_ok=snapshot.evidence_violation_count == 0,
            evidence_violation_count=snapshot.evidence_violation_count,
            outbox=snapshot.outbox,
            capacity_overlap_ok=snapshot.capacity_overlap_count == 0,
            capacity_overlap_count=snapshot.capacity_overlap_count,
            orphan_references=snapshot.orphan_references,
            future_timestamp_count=snapshot.future_timestamp_count,
            recovery_started_at=recovery_started_at,
            recovery_completed_at=completed_at,
            rto_seconds=elapsed_seconds(
                started_at=recovery_started_at,
                completed_at=completed_at,
            ),
            rto_scope=rto_scope,
            observed_data_loss_seconds=data_loss_seconds,
            rpo_evidence_basis=(
                "expected_latest_canonical_at"
                if args.expected_latest_canonical_at is not None
                and snapshot.latest_canonical_event_at is not None
                else "not_measured"
            ),
            verification_read_only=rebuild_version is None,
            ok=not issues,
            issues=issues,
        )
        document = json.dumps(
            asdict(manifest),
            default=_json_default,
            sort_keys=True,
            indent=2,
        )
        if args.manifest is not None:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(document + "\n", encoding="utf-8")
        print(document)
        if not manifest.ok:
            raise SystemExit(1)
    finally:
        engine.dispose()


def _issues(
    *,
    expected_schema_revision: str,
    restored_schema_revision: str | None,
    graph: GraphRecoveryState,
    evidence_violation_count: int,
    poisoned_event_count: int,
    capacity_overlap_count: int,
    orphan_count: int,
    future_timestamp_count: int,
    expected_latest_canonical_at: datetime | None,
    restored_latest_canonical_at: datetime | None,
) -> tuple[str, ...]:
    issues: list[str] = []
    if restored_schema_revision != expected_schema_revision:
        issues.append(
            "Alembic revision mismatch: "
            f"expected {expected_schema_revision}, restored {restored_schema_revision}"
        )
    if not graph.ok:
        issues.extend(f"graph: {issue}" for issue in graph.issues)
    if evidence_violation_count:
        issues.append(f"evidence integrity violations: {evidence_violation_count}")
    if poisoned_event_count:
        issues.append(
            "poisoned outbox events require operator disposition: "
            f"{poisoned_event_count}"
        )
    if capacity_overlap_count:
        issues.append(f"active aircraft capacity overlaps: {capacity_overlap_count}")
    if orphan_count:
        issues.append(f"foreign-key orphan references: {orphan_count}")
    if future_timestamp_count:
        issues.append(f"future canonical/evidence timestamps: {future_timestamp_count}")
    if expected_latest_canonical_at is not None and restored_latest_canonical_at is None:
        issues.append(
            "RPO evidence unavailable: expected canonical timestamp was supplied "
            "but the restored database contains no canonical event timestamp"
        )
    return tuple(issues)


def _expected_alembic_head() -> str:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic migration history has no single head")
    return head


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    try:
        return utc(parsed)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return utc(value).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value).__name__}")


if __name__ == "__main__":
    main()
