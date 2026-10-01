from __future__ import annotations

from uuid import uuid4

from charteros.observability import OperationalMetrics
from tools.http_load_assurance import _percentile, _request_value


def test_metrics_render_prometheus_with_bounded_dimensions() -> None:
    metrics = OperationalMetrics()
    metrics.api_request_started()
    metrics.api_request_finished(
        method="GET",
        route="/v1/tenders/{tender_id}",
        status_code=200,
        duration_seconds=0.125,
    )
    metrics.authorization_denial(str(uuid4()))
    metrics.resource_budget_rejection("repositioning")

    document = metrics.render_prometheus()

    assert (
        'charteros_api_requests_total{method="get",route="/v1/tenders/{tender_id}",'
        'status_class="2xx"} 1' in document
    )
    assert 'charteros_api_authorization_denials_total{reason="other"} 1' in document
    assert 'charteros_api_resource_budget_rejections_total{budget="repositioning"} 1' in document
    assert "charteros_api_in_flight_requests 0" in document


def test_metrics_record_domain_failure_signals_without_identifiers() -> None:
    metrics = OperationalMetrics()
    metrics.award(outcome="rejected", reason="commercial_conflict", duration_seconds=0.4)
    metrics.optimizer_rejection("incomplete_universe")
    metrics.evidence_integrity_failure()
    metrics.outbox_result(claimed=4, delivered=2, retried=1, poisoned=1, lease_lost=0)
    metrics.graph_operation(operation="verify", outcome="failure", duration_seconds=0.2)

    document = metrics.render_prometheus()

    assert (
        'charteros_award_attempts_total{outcome="rejected",reason="commercial_conflict"} 1'
        in document
    )
    assert 'charteros_optimizer_rejections_total{reason="incomplete_universe"} 1' in document
    assert "charteros_evidence_integrity_failures_total 1" in document
    assert "charteros_outbox_poisoned_total 1" in document
    assert "charteros_graph_verification_failures_total 1" in document


def test_load_assurance_helpers_are_deterministic_and_request_ids_are_not_reused() -> None:
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0

    first = _request_value("worker={{worker_index}};id={{request_id}}", worker_index=7)
    second = _request_value("worker={{worker_index}};id={{request_id}}", worker_index=7)

    assert first.startswith("worker=7;id=")
    assert second.startswith("worker=7;id=")
    assert first != second
