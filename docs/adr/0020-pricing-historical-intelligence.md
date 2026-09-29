# ADR 0020 — Pricing Dataset / Historical Intelligence

Status: Accepted

Date: 2026-09-29

## Context

Roadmap PR20 needs feature-ready pricing and outcome history after the deterministic PR19 market
simulator and before any production ML work. CharterOS already has canonical Mission, RFQ, Quote,
Booking, aircraft, aircraft-type, bitemporal position, Tender, and quote-normalization semantics.
PR20 must derive analytical rows from those authorities without creating a competing source of
truth or weakening the confidentiality, temporal, money, or currency rules established through
PR19.

The roadmap feature set is route, aircraft category, lead time, weekday, season, operator, position
state, quote, normalized total, accepted/rejected, and booking outcome.

## Decision

Implement PR20 as a bounded, read-only historical dataset builder over canonical PostgreSQL state.

The public programming surface is PricingIntelligenceService. A read-only CLI is provided for
deterministic extraction. PR20 does not add a public buyer/operator HTTP API, a materialized
analytics table, a feature store, or a learned model.

Dataset policy/version: pricing-dataset-v1.

Source kind: canonical_historical.

## 1. Authority boundary

PR20 never becomes transactional truth.

It does not mutate Missions, RFQs, Quotes, Bookings, Fleet history, Tenders, transactional outbox
state, Charter Graph state, or PR18 optimization state.

There is no PR20 migration. The dataset is derived from the canonical tables inside one read-only,
repeatable-read database transaction when invoked by the CLI.

## 2. Synthetic-data boundary

PR19 output is explicitly synthetic simulation evidence. PR20 does not silently combine PR19
synthetic records with canonical historical records.

PricingDataset.synthetic is always false and source_kind is canonical_historical.

A future analytics task may intentionally build a synthetic dataset from PR19, but it must remain
separately identified and must not be merged into this canonical dataset as indistinguishable
evidence.

## 3. Feature / outcome separation

PR20 deliberately separates information available at quote time from later outcome labels.

Quote-time features are route key and origin/destination ICAO, aircraft category, lead time,
departure weekday, departure month, explicit season bucket, operator identity, quote-time aircraft
position state, quote revision number, quote currency, normalized expected total, normalized
worst-case total, normalization completeness, and pricing confidence.

Later outcome labels are current canonical Quote status, accepted flag, rejected flag, whether a
Booking exists, current Booking state, and Booking creation time.

This separation prevents future labels from being silently treated as contemporaneous model inputs.
PR20 does not train a model.

## 4. Route

A route feature is the ordered pair <origin ICAO>-<destination ICAO>. Direction is significant.
The source airports remain the Mission's canonical origin and destination.

## 5. Aircraft category

Aircraft category comes from the canonical AircraftType referenced by the quoted aircraft. PR20
does not infer or reclassify aircraft categories.

## 6. Lead time

Lead time is mission.departure_window.start minus quote.submitted_at, expressed as completed integer
minutes.

A Quote submitted after the departure-window start fails closed because such a row would not be a
trustworthy pricing feature under this v1 definition.

## 7. Weekday and season

Weekday derives from the UTC mission departure-window start, with 0 = Monday through 6 = Sunday.
The lowercase weekday name is also retained.

Season is an explicit calendar transform: winter is December through February, spring is March
through May, summer is June through August, and autumn is September through November.

This is a transparent feature transform, not an empirically learned aviation-season model.

## 8. Quote-time position and no-hindsight semantics

The position feature uses the latest aircraft position observation satisfying both:

    position.event_time <= quote.submitted_at
    position.recorded_at <= quote.submitted_at

Winner ordering is deterministic:

    event_time DESC
    recorded_at DESC
    position_id DESC

Therefore a late-arriving position observation whose event time is historical but whose recorded_at
is after quote submission is not visible to the feature vector.

Position state is one of at_origin, at_other_airport, coordinates, or unknown. Coordinates means the
authoritative observation was coordinate-based rather than airport-based; PR20 does not approximate
coordinates to an airport.

Position age in completed minutes is also retained.

## 9. Quote normalization

PR20 reuses the existing canonical deterministic normalize_quote() policy. It does not implement a
second pricing formula.

For each Quote revision it records normalization version, exact quote currency, normalized expected
total, normalized worst-case total, completeness, and pricing confidence.

Quote revisions remain distinct immutable historical rows. Superseded revisions are not erased.

## 10. Money and currency boundary

All pricing values remain signed-int64 minor units with explicit Currency.

There is no floating-point money, implicit FX, hidden exchange-rate assumption, or cross-currency
global price ranking.

The dataset reports the currencies present. global_price_comparison_available is false whenever
more than one currency is present.

PR26 remains the only numbered roadmap task authorized to introduce auditable FX conversion.

## 11. Quote outcomes and Booking labels

The Quote status is treated as a later outcome label, not a quote-time feature.

PR20 exposes explicit accepted and rejected booleans while preserving the complete Quote status so
terminal outcomes such as expired, withdrawn, and superseded are not mislabeled as rejections.

An accepted Quote must have its canonical Booking. A non-accepted Quote must not be attached as the
accepted quote of a Booking. Any disagreement fails closed.

The current Booking state is an outcome label. PR20 v1 does not claim a bitemporal reconstruction of
every historical Booking state transition.

## 12. Tender confidentiality

PR20 must not become an alternate sealed-bid read surface.

Any Quote associated through a Tender invitation is excluded while that Tender is sealed and its
current status is not closed or awarded.

Unsealed Tender rows may be included.

Closed/awarded sealed Tender data may be included because the PR17 active sealed-bid confidentiality
window has ended. PR20 v1 evaluates this against current canonical Tender state; it does not claim
historical Tender-visibility replay for an arbitrary past system timestamp.

## 13. Query bounds and snapshot semantics

The source window is a UTC-aware half-open TimeRange over Quote submission time.

V1 bounds are maximum 5,000 rows and maximum source window 3,660 days.

The repository fetches limit + 1 rows to expose deterministic truncation.

CLI extraction runs under REPEATABLE READ, READ ONLY so all rows in one dataset come from one stable
PostgreSQL snapshot.

## 14. Determinism and provenance

Rows are deterministically ordered by quote.submitted_at and quote.id.

The dataset records dataset version, source kind, source window, row count, truncation state,
currency set, cross-currency comparability flag, and deterministic dataset digest.

The digest is a regression/provenance checksum over canonical serialization; it is not a digital
signature or authenticity proof.

No wall-clock timestamp is injected into the dataset payload, so identical canonical input state and
query parameters produce identical output.

## 15. CLI

Canonical historical extraction is available with:

    python -m apps.pricing_intelligence.main --window-start <UTC timestamp> --window-end <UTC timestamp> --limit <1..5000>

The CLI prints canonical JSON.

## 16. Testing

PR20 tests cover deterministic feature derivation, feature/outcome separation, established quote
normalization reuse, exact minor-unit pricing, explicit season and weekday transforms, quote-time
no-hindsight position checks, accepted Quote/Booking consistency, multi-currency isolation,
deterministic dataset digest, bounded failure behavior, PostgreSQL late-arriving position evidence,
active sealed-Tender exclusion, and the full PR1-PR19 regression suite.

## 17. Explicit non-goals

PR20 does not implement production ML, training pipelines, learned demand or price models,
auto-award or procurement decisioning, persisted feature-store infrastructure, operator portal APIs
(PR21), buyer procurement APIs (PR22), disruptions (PR23), reconciliation (PR24), audit/evidence
layer (PR25), or FX conversion/cross-currency ranking (PR26).

## Consequences

Positive consequences are canonical pricing history usable for analytics and later model
experimentation, explicit target-leakage boundaries, single-authority quote normalization,
no-hindsight position features, sealed-Tender confidentiality, and no new mutable authority.

Trade-offs are that v1 reads current canonical Quote and Booking outcomes rather than reconstructing
their state at an arbitrary historical system timestamp; closed sealed Tender rows may become
available after the confidentiality window ends; seasonality is a transparent calendar feature
rather than empirical calibration; and larger exports must partition the source window around the
5,000-row bound.
