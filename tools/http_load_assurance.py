from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RequestResult:
    status: int
    latency_seconds: float
    error: str | None


def _resolve_environment(value: object) -> object:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = os.environ.get(name)
            if resolved is None:
                raise ValueError(f"required environment variable {name!r} is not set")
            return resolved

        return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, value)
    if isinstance(value, list):
        return [_resolve_environment(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _resolve_environment(item) for key, item in value.items()}
    return value


def _request_value(value: str, *, worker_index: int) -> str:
    return value.replace("{{request_id}}", str(uuid4())).replace(
        "{{worker_index}}", str(worker_index)
    )


def _execute_request(
    *,
    base_url: str,
    request_spec: dict[str, Any],
    worker_index: int,
    timeout_seconds: float,
) -> RequestResult:
    method = str(request_spec.get("method", "GET")).upper()
    path = _request_value(str(request_spec["path"]), worker_index=worker_index)
    headers = {
        str(key): _request_value(str(value), worker_index=worker_index)
        for key, value in dict(request_spec.get("headers", {})).items()
    }
    raw_body = request_spec.get("body")
    body = None
    if raw_body is not None:
        body = _request_value(
            json.dumps(raw_body, separators=(",", ":")),
            worker_index=worker_index,
        ).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read()
            status = int(response.status)
            error = None
    except urllib.error.HTTPError as exc:
        exc.read()
        status = int(exc.code)
        error = None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status = 0
        error = type(exc).__name__
    return RequestResult(
        status=status,
        latency_seconds=time.perf_counter() - started,
        error=error,
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[rank]


def _run_phase(
    *,
    base_url: str,
    request_spec: dict[str, Any],
    concurrency: int,
    requests_per_worker: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()

    def worker(worker_index: int) -> list[RequestResult]:
        return [
            _execute_request(
                base_url=base_url,
                request_spec=request_spec,
                worker_index=worker_index,
                timeout_seconds=timeout_seconds,
            )
            for _ in range(requests_per_worker)
        ]

    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for batch in executor.map(worker, range(concurrency)):
            results.extend(batch)

    elapsed = max(time.perf_counter() - started, 1e-9)
    status_counts: dict[str, int] = {}
    for result in results:
        key = str(result.status) if result.status else "transport_error"
        status_counts[key] = status_counts.get(key, 0) + 1

    expected = {int(item) for item in request_spec.get("expected_statuses", [200])}
    failures = sum(
        1 for result in results if result.error is not None or result.status not in expected
    )
    latencies = [result.latency_seconds for result in results]
    saturation_responses = sum(1 for result in results if result.status in {429, 503})
    return {
        "concurrency": concurrency,
        "requests_per_worker": requests_per_worker,
        "attempted": len(results),
        "elapsed_seconds": elapsed,
        "throughput_requests_per_second": len(results) / elapsed,
        "status_counts": status_counts,
        "error_count": failures,
        "error_rate": failures / len(results) if results else 0.0,
        "saturation_responses": saturation_responses,
        "latency_seconds": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies, default=0.0),
        },
    }


def _run_post_check(
    *,
    base_url: str,
    check: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _execute_request(
        base_url=base_url,
        request_spec=check,
        worker_index=0,
        timeout_seconds=timeout_seconds,
    )
    expected = {int(item) for item in check.get("expected_statuses", [200])}
    return {
        "name": str(check["name"]),
        "status": result.status,
        "latency_seconds": result.latency_seconds,
        "passed": result.error is None and result.status in expected,
        "error": result.error,
    }


def run_plan(
    *,
    plan: dict[str, Any],
    base_url: str,
    timeout_seconds: float,
    evidence_class: str,
    source_sha: str,
) -> dict[str, Any]:
    if plan.get("schema_version") != "charteros.load-plan.v1":
        raise ValueError("unsupported load-plan schema")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("load plan must contain scenarios")

    scenario_results: list[dict[str, Any]] = []
    for raw_scenario in scenarios:
        if not isinstance(raw_scenario, dict):
            raise ValueError("scenario must be an object")
        scenario = dict(raw_scenario)
        request_spec = scenario.get("request")
        phases = scenario.get("phases")
        if not isinstance(request_spec, dict) or not isinstance(phases, list):
            raise ValueError(f"scenario {scenario.get('name')!r} is malformed")

        phase_results = [
            _run_phase(
                base_url=base_url,
                request_spec=request_spec,
                concurrency=int(phase["concurrency"]),
                requests_per_worker=int(phase["requests_per_worker"]),
                timeout_seconds=timeout_seconds,
            )
            for phase in phases
            if isinstance(phase, dict)
        ]
        checks = scenario.get("post_checks", [])
        post_results = [
            _run_post_check(
                base_url=base_url,
                check=check,
                timeout_seconds=timeout_seconds,
            )
            for check in checks
            if isinstance(check, dict)
        ]
        scenario_results.append(
            {
                "name": str(scenario["name"]),
                "purpose": str(scenario.get("purpose", "")),
                "phases": phase_results,
                "post_checks": post_results,
                "correctness_passed": all(item["passed"] for item in post_results),
                "first_saturation_concurrency": next(
                    (
                        int(item["concurrency"])
                        for item in phase_results
                        if int(item["saturation_responses"]) > 0
                    ),
                    None,
                ),
            }
        )

    return {
        "schema_version": "charteros.load-results.v1",
        "evidence_class": evidence_class,
        "source_sha": source_sha,
        "executed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "base_url": base_url,
        "scenarios": scenario_results,
        "all_correctness_checks_passed": all(
            bool(item["correctness_passed"]) for item in scenario_results
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible CharterOS HTTP load assurance.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--evidence-class",
        choices=("local", "staging", "production"),
        required=True,
        help="Explicitly classify where the evidence was actually executed.",
    )
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error("--source-sha must be a full 40-character Git SHA")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    raw = json.loads(args.plan.read_text(encoding="utf-8"))
    resolved = _resolve_environment(raw)
    if not isinstance(resolved, dict):
        raise ValueError("load plan root must be an object")
    result = run_plan(
        plan=resolved,
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        evidence_class=args.evidence_class,
        source_sha=args.source_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["all_correctness_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
