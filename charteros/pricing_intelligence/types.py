from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from charteros.domain.bookings import BookingState
from charteros.domain.quotes import Quote, QuoteStatus
from charteros.domain.quotes.normalization import PricingConfidence
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.time_range import TimeRange

DATASET_VERSION = "pricing-dataset-v1"
SOURCE_KIND = "canonical_historical"


class Season(StrEnum):
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"


class PositionState(StrEnum):
    UNKNOWN = "unknown"
    AT_ORIGIN = "at_origin"
    AT_OTHER_AIRPORT = "at_other_airport"
    COORDINATES = "coordinates"


@dataclass(frozen=True, slots=True)
class HistoricalPricingEvidence:
    quote: Quote
    mission_id: UUID
    operator_id: UUID
    aircraft_id: UUID
    aircraft_category: str
    origin_airport_id: UUID
    origin_icao: str
    destination_icao: str
    departure_window: TimeRange
    booking_state: BookingState | None
    booking_created_at: datetime | None
    position_airport_id: UUID | None
    position_event_time: datetime | None
    position_recorded_at: datetime | None
    position_has_coordinates: bool
    tender_id: UUID | None


@dataclass(frozen=True, slots=True)
class PricingFeatures:
    route_key: str
    origin_icao: str
    destination_icao: str
    aircraft_category: str
    lead_time_minutes: int
    departure_weekday: int
    departure_weekday_name: str
    departure_month: int
    season: Season
    operator_id: UUID
    position_state: PositionState
    position_age_minutes: int | None
    quote_revision_number: int
    normalization_version: str
    currency: Currency
    normalized_expected_total_minor: int
    normalized_worst_case_total_minor: int
    totals_complete: bool
    pricing_confidence: PricingConfidence


@dataclass(frozen=True, slots=True)
class PricingOutcomeLabels:
    quote_status: QuoteStatus
    accepted: bool
    rejected: bool
    booked: bool
    booking_state: BookingState | None
    booking_created_at: datetime | None


@dataclass(frozen=True, slots=True)
class PricingDatasetRow:
    quote_id: UUID
    rfq_id: UUID
    mission_id: UUID
    aircraft_id: UUID
    quote_submitted_at: datetime
    departure_window: TimeRange
    tender_id: UUID | None
    features: PricingFeatures
    outcomes: PricingOutcomeLabels


@dataclass(frozen=True, slots=True)
class PricingDataset:
    dataset_version: str
    source_kind: str
    source_window: TimeRange
    row_count: int
    truncated: bool
    currencies: tuple[Currency, ...]
    global_price_comparison_available: bool
    dataset_digest: str
    rows: tuple[PricingDatasetRow, ...]
    synthetic: bool = field(default=False, init=False)
