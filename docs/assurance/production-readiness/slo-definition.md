# Candidate production SLOs

**Status:** candidate measurement contract, not production certification.

PR48 defines what CharterOS can measure and the first thresholds to validate in staging. These
numbers are not claims that production has achieved them. Production SLOs must be accepted only
after a representative staging soak and then measured in the target environment.

| SLO | Candidate target | Measurement |
| --- | --- | --- |
| API availability | >= 99.5% over 28 days | non-5xx `charteros_api_requests_total` / all API requests; planned maintenance excluded by operating policy |
| API latency | p95 < 500 ms over 30 min | `charteros_api_request_duration_seconds`, evaluated by bounded route template |
| Award latency | p95 < 2 s over 30 min | `charteros_award_duration_seconds{outcome="success"}` |
| Outbox delivery | oldest pending age < 60 s for 99% of 5-minute samples | `charteros_outbox_oldest_pending_age_seconds` |
| Graph projection lag | < 60 s for 99% of 5-minute samples | `charteros_graph_projection_lag_seconds` |
| Evidence generation | p95 < 5 s over 30 min | `charteros_evidence_generation_duration_seconds{outcome="success"}` |

The thresholds are deliberately moderate initial candidates. They must be revised from observed
capacity and business requirements, not tightened for presentation value.

## Error-budget interpretation

A 5xx is an availability failure. Authentication/authorization denials, bounded request rejection,
and explicit 409 business conflicts are not availability failures, but they are separately measured
so abuse or caller mistakes cannot be hidden inside availability.

## Cardinality contract

Metrics may label only bounded dimensions such as HTTP method, route template, status class,
principal-denial reason, abuse budget, database failure class, award outcome, evidence subject type,
or outbox state.

Never use Mission UUIDs, Booking UUIDs, tender/quote IDs, aircraft registration, tenant IDs,
idempotency keys, JWT subjects, correlation IDs, or free-form exception text as metric labels.
Those belong in structured logs/traces.

## Measurement queries

Examples:

```promql
sum(rate(charteros_api_requests_total{status_class!="5xx"}[28d]))
/
sum(rate(charteros_api_requests_total[28d]))
```

```promql
histogram_quantile(
  0.95,
  sum by (le, route) (rate(charteros_api_request_duration_seconds_bucket[30m]))
)
```

```promql
histogram_quantile(
  0.95,
  sum by (le) (rate(charteros_award_duration_seconds_bucket{outcome="success"}[30m]))
)
```

No candidate SLO is considered certified until real staging/production evidence is retained.
