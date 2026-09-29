from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import BookingState
from charteros.domain.quotes import (
    PriceComponent,
    PriceComponentApplicability,
    PriceComponentCategory,
    Quote,
    QuoteId,
    QuoteStatus,
)
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.pricing_intelligence import (
    HistoricalPricingEvidence,
    PositionState,
    Season,
    build_pricing_dataset,
    build_pricing_row,
    digest_dataset,
)

BASE = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
EUR = Currency("EUR")
USD = Currency("USD")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _quote(
    *,
    seed: int = 1,
    currency: Currency = EUR,
    status: QuoteStatus = QuoteStatus.SUBMITTED,
) -> Quote:
    accepted_at = BASE + timedelta(hours=1) if status is QuoteStatus.ACCEPTED else None
    rejected_at = BASE + timedelta(hours=1) if status is QuoteStatus.REJECTED else None
    return Quote(
        QuoteId(_id(1000 + seed)),
        rfq_id=RfqId(_id(2000 + seed)),
        aircraft_id=AircraftId(_id(3000 + seed)),
        base_price=Money(1_000_000, currency),
        price_components=(
            PriceComponent(
                category=PriceComponentCategory.HANDLING,
                label="Handling",
                amount=Money(50_000, currency),
            ),
            PriceComponent(
                category=PriceComponentCategory.DEICING,
                label="Possible deicing",
                amount=Money(25_000, currency),
                applicability=PriceComponentApplicability.CONDITIONAL,
                condition="Forecast requires deicing",
            ),
        ),
        repositioning_cost=Money(100_000, currency),
        inclusions=(),
        exclusions=(),
        cancellation_terms=None,
        payment_terms=None,
        valid_until=BASE + timedelta(days=2),
        status=status,
        revision_number=1,
        supersedes_quote_id=None,
        submitted_at=BASE,
        is_current=status is QuoteStatus.SUBMITTED,
        accepted_at=accepted_at,
        rejected_at=rejected_at,
    )


def _evidence(
    *,
    seed: int = 1,
    currency: Currency = EUR,
    status: QuoteStatus = QuoteStatus.SUBMITTED,
    departure: datetime | None = None,
) -> HistoricalPricingEvidence:
    quote = _quote(seed=seed, currency=currency, status=status)
    booked = status is QuoteStatus.ACCEPTED
    return HistoricalPricingEvidence(
        quote=quote,
        mission_id=_id(4000 + seed),
        operator_id=_id(5000 + seed),
        aircraft_id=quote.aircraft_id.value,
        aircraft_category="midsize",
        origin_airport_id=_id(6000 + seed),
        origin_icao="LGAV",
        destination_icao="LGTS",
        departure_window=TimeRange(
            departure or BASE + timedelta(days=10),
            (departure or BASE + timedelta(days=10)) + timedelta(hours=2),
        ),
        booking_state=BookingState.CONFIRMED if booked else None,
        booking_created_at=BASE + timedelta(hours=1) if booked else None,
        position_airport_id=_id(6000 + seed),
        position_event_time=BASE - timedelta(hours=2),
        position_recorded_at=BASE - timedelta(hours=1),
        position_has_coordinates=False,
        tender_id=None,
    )


def test_feature_row_separates_quote_time_features_from_later_outcomes() -> None:
    row = build_pricing_row(_evidence(status=QuoteStatus.ACCEPTED))

    assert row.features.route_key == "LGAV-LGTS"
    assert row.features.aircraft_category == "midsize"
    assert row.features.lead_time_minutes == 10 * 24 * 60
    assert row.features.departure_weekday_name == "friday"
    assert row.features.departure_month == 10
    assert row.features.season is Season.AUTUMN
    assert row.features.position_state is PositionState.AT_ORIGIN
    assert row.features.position_age_minutes == 120
    assert row.features.normalized_expected_total_minor == 1_150_000
    assert row.features.normalized_worst_case_total_minor == 1_175_000
    assert row.features.totals_complete is True

    assert row.outcomes.quote_status is QuoteStatus.ACCEPTED
    assert row.outcomes.accepted is True
    assert row.outcomes.rejected is False
    assert row.outcomes.booked is True
    assert row.outcomes.booking_state is BookingState.CONFIRMED


def test_position_hindsight_is_rejected_even_when_event_time_is_historical() -> None:
    evidence = _evidence()
    hindsight = HistoricalPricingEvidence(
        quote=evidence.quote,
        mission_id=evidence.mission_id,
        operator_id=evidence.operator_id,
        aircraft_id=evidence.aircraft_id,
        aircraft_category=evidence.aircraft_category,
        origin_airport_id=evidence.origin_airport_id,
        origin_icao=evidence.origin_icao,
        destination_icao=evidence.destination_icao,
        departure_window=evidence.departure_window,
        booking_state=evidence.booking_state,
        booking_created_at=evidence.booking_created_at,
        position_airport_id=evidence.position_airport_id,
        position_event_time=BASE - timedelta(hours=3),
        position_recorded_at=BASE + timedelta(minutes=1),
        position_has_coordinates=False,
        tender_id=None,
    )

    with pytest.raises(EntityConflictError, match="hindsight"):
        build_pricing_row(hindsight)


def test_accepted_quote_requires_booking_and_nonaccepted_quote_cannot_have_one() -> None:
    accepted = _evidence(status=QuoteStatus.ACCEPTED)
    missing_booking = HistoricalPricingEvidence(
        quote=accepted.quote,
        mission_id=accepted.mission_id,
        operator_id=accepted.operator_id,
        aircraft_id=accepted.aircraft_id,
        aircraft_category=accepted.aircraft_category,
        origin_airport_id=accepted.origin_airport_id,
        origin_icao=accepted.origin_icao,
        destination_icao=accepted.destination_icao,
        departure_window=accepted.departure_window,
        booking_state=None,
        booking_created_at=None,
        position_airport_id=accepted.position_airport_id,
        position_event_time=accepted.position_event_time,
        position_recorded_at=accepted.position_recorded_at,
        position_has_coordinates=False,
        tender_id=None,
    )
    with pytest.raises(EntityConflictError, match="booking outcome disagree"):
        build_pricing_row(missing_booking)

    submitted = _evidence()
    impossible_booking = HistoricalPricingEvidence(
        quote=submitted.quote,
        mission_id=submitted.mission_id,
        operator_id=submitted.operator_id,
        aircraft_id=submitted.aircraft_id,
        aircraft_category=submitted.aircraft_category,
        origin_airport_id=submitted.origin_airport_id,
        origin_icao=submitted.origin_icao,
        destination_icao=submitted.destination_icao,
        departure_window=submitted.departure_window,
        booking_state=BookingState.PENDING_CONTRACT,
        booking_created_at=BASE + timedelta(hours=1),
        position_airport_id=submitted.position_airport_id,
        position_event_time=submitted.position_event_time,
        position_recorded_at=submitted.position_recorded_at,
        position_has_coordinates=False,
        tender_id=None,
    )
    with pytest.raises(EntityConflictError, match="booking outcome disagree"):
        build_pricing_row(impossible_booking)


def test_multicurrency_dataset_is_partitioned_without_global_price_comparison() -> None:
    window = TimeRange(BASE - timedelta(days=1), BASE + timedelta(days=30))
    evidence = (
        _evidence(seed=2, currency=USD),
        _evidence(seed=1, currency=EUR),
    )

    first = build_pricing_dataset(
        source_window=window,
        evidence=evidence,
        truncated=False,
    )
    second = build_pricing_dataset(
        source_window=window,
        evidence=evidence,
        truncated=False,
    )

    assert first == second
    assert first.row_count == 2
    assert [str(currency) for currency in first.currencies] == ["EUR", "USD"]
    assert first.global_price_comparison_available is False
    assert first.dataset_digest == second.dataset_digest
    assert first.dataset_digest == digest_dataset(first)
    assert first.synthetic is False
    assert all(
        row.features.normalized_expected_total_minor == 1_150_000 for row in first.rows
    )


def test_quote_after_departure_start_fails_closed() -> None:
    evidence = _evidence(departure=BASE - timedelta(minutes=1))
    with pytest.raises(EntityConflictError, match="after the mission departure"):
        build_pricing_row(evidence)
