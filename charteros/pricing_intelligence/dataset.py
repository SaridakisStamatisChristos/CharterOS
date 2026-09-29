from __future__ import annotations

import json
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from uuid import UUID

from charteros.application.exceptions import EntityConflictError
from charteros.domain.quotes import QuoteStatus
from charteros.domain.quotes.normalization import normalize_quote
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.time_range import TimeRange
from charteros.pricing_intelligence.types import (
    DATASET_VERSION,
    SOURCE_KIND,
    HistoricalPricingEvidence,
    PositionState,
    PricingDataset,
    PricingDatasetRow,
    PricingFeatures,
    PricingOutcomeLabels,
    Season,
)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _season(month: int) -> Season:
    if month in (12, 1, 2):
        return Season.WINTER
    if month in (3, 4, 5):
        return Season.SPRING
    if month in (6, 7, 8):
        return Season.SUMMER
    return Season.AUTUMN


def _position_features(
    evidence: HistoricalPricingEvidence,
) -> tuple[PositionState, int | None]:
    if evidence.position_event_time is None:
        if (
            evidence.position_recorded_at is not None
            or evidence.position_airport_id is not None
            or evidence.position_has_coordinates
        ):
            raise EntityConflictError("position evidence is internally inconsistent")
        return PositionState.UNKNOWN, None

    event_time = _utc(evidence.position_event_time)
    submitted_at = _utc(evidence.quote.submitted_at)
    if event_time > submitted_at:
        raise EntityConflictError("pricing dataset position uses future event-time evidence")
    if evidence.position_recorded_at is None:
        raise EntityConflictError("position event is missing recorded_at")
    recorded_at = _utc(evidence.position_recorded_at)
    if recorded_at > submitted_at:
        raise EntityConflictError("pricing dataset position uses hindsight knowledge")

    if evidence.position_airport_id == evidence.origin_airport_id:
        state = PositionState.AT_ORIGIN
    elif evidence.position_airport_id is not None:
        state = PositionState.AT_OTHER_AIRPORT
    elif evidence.position_has_coordinates:
        state = PositionState.COORDINATES
    else:
        raise EntityConflictError("position evidence has no location representation")

    age_seconds = int((submitted_at - event_time).total_seconds())
    if age_seconds < 0:
        raise EntityConflictError("position age cannot be negative")
    return state, age_seconds // 60


def build_pricing_row(evidence: HistoricalPricingEvidence) -> PricingDatasetRow:
    quote = evidence.quote
    if quote.aircraft_id.value != evidence.aircraft_id:
        raise EntityConflictError("quote aircraft conflicts with pricing evidence")
    if not evidence.aircraft_category.strip():
        raise EntityConflictError("aircraft category cannot be blank")

    submitted_at = _utc(quote.submitted_at)
    departure_from = _utc(evidence.departure_window.start)
    lead_seconds = int((departure_from - submitted_at).total_seconds())
    if lead_seconds < 0:
        raise EntityConflictError("quote was submitted after the mission departure window started")

    normalization = normalize_quote(quote)
    position_state, position_age_minutes = _position_features(evidence)
    accepted = quote.status is QuoteStatus.ACCEPTED
    rejected = quote.status is QuoteStatus.REJECTED
    booked = evidence.booking_state is not None

    if accepted != booked:
        raise EntityConflictError(
            "accepted quote and booking outcome disagree in historical pricing evidence"
        )
    if booked and evidence.booking_created_at is None:
        raise EntityConflictError("booked pricing evidence is missing booking_created_at")
    if not booked and evidence.booking_created_at is not None:
        raise EntityConflictError("unbooked pricing evidence unexpectedly has booking_created_at")
    if evidence.booking_created_at is not None:
        booking_created_at = _utc(evidence.booking_created_at)
        if booking_created_at < submitted_at:
            raise EntityConflictError("booking creation cannot precede quote submission")
    else:
        booking_created_at = None

    weekday = departure_from.weekday()
    return PricingDatasetRow(
        quote_id=quote.id.value,
        rfq_id=quote.rfq_id.value,
        mission_id=evidence.mission_id,
        aircraft_id=evidence.aircraft_id,
        quote_submitted_at=submitted_at,
        departure_window=evidence.departure_window,
        tender_id=evidence.tender_id,
        features=PricingFeatures(
            route_key=f"{evidence.origin_icao}-{evidence.destination_icao}",
            origin_icao=evidence.origin_icao,
            destination_icao=evidence.destination_icao,
            aircraft_category=evidence.aircraft_category,
            lead_time_minutes=lead_seconds // 60,
            departure_weekday=weekday,
            departure_weekday_name=departure_from.strftime("%A").lower(),
            departure_month=departure_from.month,
            season=_season(departure_from.month),
            operator_id=evidence.operator_id,
            position_state=position_state,
            position_age_minutes=position_age_minutes,
            quote_revision_number=quote.revision_number,
            normalization_version=normalization.normalization_version,
            currency=normalization.currency,
            normalized_expected_total_minor=normalization.expected_total.amount_minor,
            normalized_worst_case_total_minor=normalization.worst_case_total.amount_minor,
            totals_complete=normalization.totals_complete,
            pricing_confidence=normalization.confidence,
        ),
        outcomes=PricingOutcomeLabels(
            quote_status=quote.status,
            accepted=accepted,
            rejected=rejected,
            booked=booked,
            booking_state=evidence.booking_state,
            booking_created_at=booking_created_at,
        ),
    )


def _canonical(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Currency):
        return value.code
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, TimeRange):
        return {"start": _canonical(value.start), "end": _canonical(value.end)}
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    return value


def canonical_json(value: object) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_dataset(value: PricingDataset) -> str:
    return sha256(canonical_json(replace(value, dataset_digest="")).encode()).hexdigest()


def build_pricing_dataset(
    *,
    source_window: TimeRange,
    evidence: tuple[HistoricalPricingEvidence, ...],
    truncated: bool,
) -> PricingDataset:
    rows = tuple(
        sorted(
            (build_pricing_row(item) for item in evidence),
            key=lambda item: (item.quote_submitted_at, item.quote_id.hex),
        )
    )
    currencies = tuple(sorted({item.features.currency for item in rows}))
    provisional = PricingDataset(
        dataset_version=DATASET_VERSION,
        source_kind=SOURCE_KIND,
        source_window=source_window,
        row_count=len(rows),
        truncated=truncated,
        currencies=currencies,
        global_price_comparison_available=len(currencies) <= 1,
        dataset_digest="",
        rows=rows,
    )
    return replace(provisional, dataset_digest=digest_dataset(provisional))
