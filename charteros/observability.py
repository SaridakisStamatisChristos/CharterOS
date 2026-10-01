from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from time import perf_counter
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from charteros.application.graph_projection import PROJECTION_NAME
from charteros.infrastructure.db.models.catalog import OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphProjectionCheckpointRow,
    GraphProjectionVersionRow,
)

_SECONDS_BUCKETS: Final[tuple[float, ...]] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
_COUNT_BUCKETS: Final[tuple[float, ...]] = (
    0.0,
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1_000.0,
    2_000.0,
)


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    buckets: tuple[float, ...]
    counts: tuple[int, ...]
    count: int
    total: float


class OperationalMetrics:
    """Small process-local Prometheus registry with bounded label surfaces.

    Only fixed, code-controlled dimensions are accepted by the typed helper methods below.
    Correlation IDs, tenant IDs, mission/booking IDs, subjects, aircraft registrations, and
    free-form exception messages must never be passed into this registry.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], tuple[tuple[float, ...], list[int], int, float]
        ] = {}

    def api_request_started(self) -> None:
        self._add_gauge("charteros_api_in_flight_requests", 1.0)

    def api_request_finished(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        self._add_gauge("charteros_api_in_flight_requests", -1.0)
        status_class = f"{status_code // 100}xx"
        labels = {"method": method.upper(), "route": route, "status_class": status_class}
        self._inc("charteros_api_requests_total", labels)
        if status_code >= 500:
            self._inc("charteros_api_errors_total", {"status_class": status_class})
        self._observe(
            "charteros_api_request_duration_seconds",
            {"method": method.upper(), "route": route},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )

    def api_rejection(self, reason: str) -> None:
        self._inc("charteros_api_request_rejections_total", {"reason": _bounded(reason)})

    def auth_failure(self, reason: str) -> None:
        self._inc("charteros_api_auth_failures_total", {"reason": _bounded(reason)})

    def authorization_denial(self, reason: str) -> None:
        self._inc("charteros_api_authorization_denials_total", {"reason": _bounded(reason)})

    def resource_budget_rejection(self, budget: str) -> None:
        self._inc("charteros_api_resource_budget_rejections_total", {"budget": _bounded(budget)})

    def db_pool_checkout(self, *, duration_seconds: float, outcome: str) -> None:
        self._observe(
            "charteros_db_pool_checkout_duration_seconds",
            {"outcome": _bounded(outcome)},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )
        if outcome != "success":
            self._inc("charteros_db_pool_checkout_failures_total", {"reason": _bounded(outcome)})

    def db_pool_state(self, *, checked_out: int, pool_size: int, overflow: int) -> None:
        self._set("charteros_db_pool_checked_out", float(max(checked_out, 0)))
        self._set("charteros_db_pool_size", float(max(pool_size, 0)))
        self._set("charteros_db_pool_overflow", float(max(overflow, 0)))

    def db_transaction(
        self,
        *,
        duration_seconds: float,
        outcome: str,
        failure_kind: str = "none",
    ) -> None:
        self._observe(
            "charteros_db_transaction_duration_seconds",
            {"outcome": _bounded(outcome)},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )
        if outcome != "success":
            self._inc(
                "charteros_db_transaction_failures_total",
                {"kind": _bounded(failure_kind)},
            )

    def db_retry(self, reason: str) -> None:
        self._inc("charteros_db_transaction_retries_total", {"reason": _bounded(reason)})

    def db_deadlock(self) -> None:
        self._inc("charteros_db_deadlocks_total")

    def db_lock_wait_failure(self) -> None:
        self._inc("charteros_db_lock_wait_failures_total")

    def db_connectivity_failure(self) -> None:
        self._inc("charteros_db_connectivity_failures_total")

    def ambiguous_commit_recovered(self) -> None:
        self._inc("charteros_db_ambiguous_commit_recoveries_total")

    def award(self, *, outcome: str, reason: str, duration_seconds: float) -> None:
        labels = {"outcome": _bounded(outcome), "reason": _bounded(reason)}
        self._inc("charteros_award_attempts_total", labels)
        self._observe(
            "charteros_award_duration_seconds",
            {"outcome": _bounded(outcome)},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )

    def outbox_result(
        self,
        *,
        claimed: int,
        delivered: int,
        retried: int,
        poisoned: int,
        lease_lost: int,
    ) -> None:
        self._inc("charteros_outbox_claimed_total", amount=float(claimed))
        self._inc("charteros_outbox_delivered_total", amount=float(delivered))
        self._inc("charteros_outbox_retried_total", amount=float(retried))
        self._inc("charteros_outbox_poisoned_total", amount=float(poisoned))
        self._inc("charteros_outbox_lease_lost_total", amount=float(lease_lost))

    def outbox_delivery_latency(self, seconds: float) -> None:
        self._observe(
            "charteros_outbox_delivery_latency_seconds",
            {},
            max(seconds, 0.0),
            _SECONDS_BUCKETS,
        )

    def outbox_dedupe_hit(self) -> None:
        self._inc("charteros_outbox_consumer_dedupe_hits_total")

    def graph_operation(self, *, operation: str, outcome: str, duration_seconds: float) -> None:
        self._observe(
            "charteros_graph_operation_duration_seconds",
            {"operation": _bounded(operation), "outcome": _bounded(outcome)},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )
        if operation == "verify" and outcome != "success":
            self._inc("charteros_graph_verification_failures_total")

    def optimizer_run(
        self,
        *,
        candidate_count: int,
        quoted_opportunity_count: int,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        self._observe(
            "charteros_optimizer_candidate_count",
            {"outcome": _bounded(outcome)},
            float(max(candidate_count, 0)),
            _COUNT_BUCKETS,
        )
        self._observe(
            "charteros_optimizer_quoted_opportunity_count",
            {"outcome": _bounded(outcome)},
            float(max(quoted_opportunity_count, 0)),
            _COUNT_BUCKETS,
        )
        self._observe(
            "charteros_optimizer_runtime_seconds",
            {"outcome": _bounded(outcome)},
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )

    def optimizer_rejection(self, reason: str) -> None:
        self._inc("charteros_optimizer_rejections_total", {"reason": _bounded(reason)})

    def evidence_build(
        self,
        *,
        subject_type: str,
        outcome: str,
        completeness: str,
        duration_seconds: float,
    ) -> None:
        self._observe(
            "charteros_evidence_generation_duration_seconds",
            {
                "subject_type": _bounded(subject_type),
                "outcome": _bounded(outcome),
                "completeness": _bounded(completeness),
            },
            max(duration_seconds, 0.0),
            _SECONDS_BUCKETS,
        )
        if completeness != "complete":
            self._inc(
                "charteros_evidence_incomplete_packages_total",
                {"completeness": _bounded(completeness)},
            )

    def evidence_integrity_failure(self) -> None:
        self._inc("charteros_evidence_integrity_failures_total")

    def decision_snapshot_write_failure(self) -> None:
        self._inc("charteros_decision_snapshot_write_failures_total")

    def set_persistent_state(
        self,
        *,
        outbox_counts: dict[str, int],
        oldest_pending_age_seconds: float,
        active_projection_version: int | None,
        graph_projection_lag_seconds: float,
    ) -> None:
        for status in ("pending", "retry", "in_flight", "poisoned"):
            self._set(
                "charteros_outbox_events",
                float(max(outbox_counts.get(status, 0), 0)),
                {"status": status},
            )
        self._set(
            "charteros_outbox_oldest_pending_age_seconds",
            max(oldest_pending_age_seconds, 0.0),
        )
        self._set(
            "charteros_graph_active_projection_version",
            float(active_projection_version or 0),
        )
        self._set(
            "charteros_graph_projection_lag_seconds",
            max(graph_projection_lag_seconds, 0.0),
        )

    def render_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []
            grouped: dict[str, str] = {}
            for name, _labels in self._counters:
                grouped[name] = "counter"
            for name, _labels in self._gauges:
                grouped[name] = "gauge"
            for name, _labels in self._histograms:
                grouped[name] = "histogram"

            for name in sorted(grouped):
                lines.append(f"# TYPE {name} {grouped[name]}")
                if grouped[name] == "counter":
                    for (metric_name, labels), value in sorted(self._counters.items()):
                        if metric_name == name:
                            lines.append(f"{name}{_render_labels(labels)} {value:g}")
                elif grouped[name] == "gauge":
                    for (metric_name, labels), value in sorted(self._gauges.items()):
                        if metric_name == name:
                            lines.append(f"{name}{_render_labels(labels)} {value:g}")
                else:
                    for (metric_name, labels), data in sorted(self._histograms.items()):
                        if metric_name != name:
                            continue
                        buckets, counts, count, total = data
                        cumulative = 0
                        for boundary, bucket_count in zip(buckets, counts, strict=True):
                            cumulative += bucket_count
                            bucket_labels = (*labels, ("le", f"{boundary:g}"))
                            lines.append(
                                f"{name}_bucket{_render_labels(bucket_labels)} {cumulative}"
                            )
                        inf_labels = (*labels, ("le", "+Inf"))
                        lines.append(f"{name}_bucket{_render_labels(inf_labels)} {count}")
                        lines.append(f"{name}_count{_render_labels(labels)} {count}")
                        lines.append(f"{name}_sum{_render_labels(labels)} {total:g}")
            return "\n".join(lines) + "\n"

    def _inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        *,
        amount: float = 1.0,
    ) -> None:
        if amount <= 0:
            return
        key = (name, _label_key(labels))
        with self._lock:
            self._counters[key] += amount

    def _set(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = (name, _label_key(labels))
        with self._lock:
            self._gauges[key] = value

    def _add_gauge(self, name: str, delta: float) -> None:
        key = (name, ())
        with self._lock:
            self._gauges[key] = max(0.0, self._gauges.get(key, 0.0) + delta)

    def _observe(
        self,
        name: str,
        labels: dict[str, str],
        value: float,
        buckets: tuple[float, ...],
    ) -> None:
        key = (name, _label_key(labels))
        with self._lock:
            current = self._histograms.get(key)
            if current is None:
                counts = [0 for _ in buckets]
                count = 0
                total = 0.0
            else:
                _, counts, count, total = current
                counts = list(counts)
            for index, boundary in enumerate(buckets):
                if value <= boundary:
                    counts[index] += 1
                    break
            count += 1
            total += value
            self._histograms[key] = (buckets, counts, count, total)


_DEFAULT_METRICS = OperationalMetrics()


def get_operational_metrics() -> OperationalMetrics:
    return _DEFAULT_METRICS


def install_operational_metrics(metrics: OperationalMetrics) -> None:
    global _DEFAULT_METRICS
    _DEFAULT_METRICS = metrics


def collect_persistent_metrics(
    session: Session,
    metrics: OperationalMetrics,
    *,
    now: datetime | None = None,
) -> None:
    when = (now or datetime.now(UTC)).astimezone(UTC)
    rows = session.execute(
        select(OutboxEventRow.delivery_status, func.count())
        .where(OutboxEventRow.delivery_status.in_(("pending", "retry", "in_flight", "poisoned")))
        .group_by(OutboxEventRow.delivery_status)
    ).all()
    outbox_counts = {str(status): int(count) for status, count in rows}

    oldest = session.scalar(
        select(func.min(OutboxEventRow.recorded_at)).where(
            OutboxEventRow.delivery_status.in_(("pending", "retry"))
        )
    )
    oldest_age = 0.0 if oldest is None else max(0.0, (when - oldest).total_seconds())

    active = session.scalar(
        select(GraphProjectionVersionRow).where(GraphProjectionVersionRow.active_key == "active")
    )
    active_version = active.projection_version if active is not None else None
    checkpoint = None
    if active_version is not None:
        checkpoint = session.get(
            GraphProjectionCheckpointRow,
            (PROJECTION_NAME, active_version),
        )
    lag = (
        0.0
        if checkpoint is None or checkpoint.max_recorded_at is None
        else max(0.0, (when - checkpoint.max_recorded_at).total_seconds())
    )
    metrics.set_persistent_state(
        outbox_counts=outbox_counts,
        oldest_pending_age_seconds=oldest_age,
        active_projection_version=active_version,
        graph_projection_lag_seconds=lag,
    )


def monotonic_seconds() -> float:
    return perf_counter()


def _bounded(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    if not normalized or len(normalized) > 64:
        return "other"
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-." for character in normalized):
        return "other"
    try:
        UUID(normalized)
    except ValueError:
        return normalized
    return "other"


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(
        sorted(
            (
                key,
                _bounded_route(value) if key == "route" else _bounded(value),
            )
            for key, value in labels.items()
        )
    )


def _bounded_route(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 192:
        return "unmatched"
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_-/{}:."
    if any(character not in allowed for character in normalized):
        return "unmatched"
    return normalized


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + rendered + "}"
