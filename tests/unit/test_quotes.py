from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.aircraft import AircraftId
from charteros.domain.quotes import (
    PriceComponent,
    PriceComponentCategory,
    Quote,
    QuoteStatus,
)
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError, MoneyOverflowError
from charteros.domain.shared.money import Money

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
VALID_UNTIL = NOW + timedelta(days=2)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _quote() -> Quote:
    eur = Currency("EUR")
    return Quote.submit(
        rfq_id=RfqId(_id(1)),
        aircraft_id=AircraftId(_id(2)),
        base_price=Money(7_400_000, eur),
        price_components=(
            PriceComponent(
                category=PriceComponentCategory.HANDLING,
                label="Airport handling",
                amount=Money(125_000, eur),
            ),
            PriceComponent(
                category=PriceComponentCategory.TAXES,
                label="Taxes",
                amount=Money(50_000, eur),
            ),
        ),
        repositioning_cost=Money(300_000, eur),
        inclusions=("  Catering  ", "WiFi"),
        exclusions=("Deicing",),
        cancellation_terms="  25% until 72 hours  ",
        payment_terms="  50% on confirmation  ",
        valid_until=VALID_UNTIL,
        submitted_at=NOW,
    )


def test_submission_is_structured_exact_and_canonical() -> None:
    quote = _quote()

    assert quote.status is QuoteStatus.SUBMITTED
    assert quote.version == 1
    assert quote.is_current is True
    assert quote.currency == Currency("EUR")
    assert quote.submitted_total == Money(7_875_000, Currency("EUR"))
    assert quote.inclusions == ("Catering", "WiFi")
    assert quote.exclusions == ("Deicing",)
    assert quote.cancellation_terms == "25% until 72 hours"
    assert quote.payment_terms == "50% on confirmation"
    assert quote.pending_events[-1].event_type == "QUOTE_SUBMITTED"


def test_mixed_currency_component_and_repositioning_are_rejected() -> None:
    eur = Currency("EUR")
    usd = Currency("USD")
    component = PriceComponent(
        category=PriceComponentCategory.FUEL_SURCHARGE,
        label="Fuel",
        amount=Money(100, usd),
    )
    with pytest.raises(DomainValidationError, match="component currencies"):
        Quote.submit(
            rfq_id=RfqId(_id(1)),
            aircraft_id=AircraftId(_id(2)),
            base_price=Money(1_000, eur),
            price_components=(component,),
            repositioning_cost=None,
            inclusions=(),
            exclusions=(),
            cancellation_terms=None,
            payment_terms=None,
            valid_until=VALID_UNTIL,
            submitted_at=NOW,
        )

    with pytest.raises(DomainValidationError, match="repositioning_cost currency"):
        Quote.submit(
            rfq_id=RfqId(_id(1)),
            aircraft_id=AircraftId(_id(2)),
            base_price=Money(1_000, eur),
            price_components=(),
            repositioning_cost=Money(100, usd),
            inclusions=(),
            exclusions=(),
            cancellation_terms=None,
            payment_terms=None,
            valid_until=VALID_UNTIL,
            submitted_at=NOW,
        )


def test_total_overflow_rejects_submission_explicitly() -> None:
    eur = Currency("EUR")
    with pytest.raises(MoneyOverflowError):
        Quote.submit(
            rfq_id=RfqId(_id(1)),
            aircraft_id=AircraftId(_id(2)),
            base_price=Money(2**63 - 1, eur),
            price_components=(
                PriceComponent(
                    category=PriceComponentCategory.OTHER,
                    label="One more cent",
                    amount=Money(1, eur),
                ),
            ),
            repositioning_cost=None,
            inclusions=(),
            exclusions=(),
            cancellation_terms=None,
            payment_terms=None,
            valid_until=VALID_UNTIL,
            submitted_at=NOW,
        )


def _submit_simple_quote(*, valid_until: datetime, submitted_at: datetime) -> Quote:
    return Quote.submit(
        rfq_id=RfqId(_id(1)),
        aircraft_id=AircraftId(_id(2)),
        base_price=Money(1_000, Currency("EUR")),
        price_components=(),
        repositioning_cost=None,
        inclusions=(),
        exclusions=(),
        cancellation_terms=None,
        payment_terms=None,
        valid_until=valid_until,
        submitted_at=submitted_at,
    )


def test_naive_or_impossible_validity_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        _submit_simple_quote(
            valid_until=datetime(2026, 9, 30, 12),
            submitted_at=NOW,
        )
    with pytest.raises(DomainValidationError, match="after submitted_at"):
        _submit_simple_quote(
            valid_until=NOW,
            submitted_at=NOW,
        )


def test_inclusion_exclusion_deduplication_and_overlap() -> None:
    eur = Currency("EUR")
    quote = Quote.submit(
        rfq_id=RfqId(_id(1)),
        aircraft_id=AircraftId(_id(2)),
        base_price=Money(1_000, eur),
        price_components=(),
        repositioning_cost=None,
        inclusions=(" WiFi ", "wifi", "Catering"),
        exclusions=("Deicing", " deicing "),
        cancellation_terms=None,
        payment_terms=None,
        valid_until=VALID_UNTIL,
        submitted_at=NOW,
    )
    assert quote.inclusions == ("WiFi", "Catering")
    assert quote.exclusions == ("Deicing",)

    with pytest.raises(DomainValidationError, match="included and excluded"):
        Quote.submit(
            rfq_id=RfqId(_id(1)),
            aircraft_id=AircraftId(_id(2)),
            base_price=Money(1_000, eur),
            price_components=(),
            repositioning_cost=None,
            inclusions=("WiFi",),
            exclusions=(" wifi ",),
            cancellation_terms=None,
            payment_terms=None,
            valid_until=VALID_UNTIL,
            submitted_at=NOW,
        )


def test_revision_preserves_lineage_and_prior_commercial_terms() -> None:
    first = _quote()
    first_base = first.base_price
    revision_time = NOW + timedelta(hours=1)
    replacement = Quote.revise(
        first,
        aircraft_id=AircraftId(_id(3)),
        base_price=Money(7_200_000, Currency("EUR")),
        price_components=(),
        repositioning_cost=None,
        inclusions=("Catering",),
        exclusions=("Deicing",),
        cancellation_terms="20% until 72 hours",
        payment_terms="Full payment before departure",
        valid_until=VALID_UNTIL + timedelta(hours=1),
        submitted_at=revision_time,
    )

    assert replacement.revision_number == 2
    assert replacement.supersedes_quote_id == first.id
    assert replacement.pending_events[-1].event_type == "QUOTE_REVISED"
    assert first.base_price == first_base

    first.supersede(
        superseded_at=revision_time,
        replacement_quote_id=replacement.id,
    )
    assert first.status is QuoteStatus.SUPERSEDED
    assert first.is_current is False
    assert first.pending_events[-1].event_type == "QUOTE_SUPERSEDED"


def test_withdrawal_and_expiry_are_explicit_single_use_transitions() -> None:
    withdrawn = _quote()
    withdrawn.withdraw(withdrawn_at=NOW + timedelta(hours=1))
    assert withdrawn.status is QuoteStatus.WITHDRAWN
    assert withdrawn.is_current is False
    assert withdrawn.pending_events[-1].event_type == "QUOTE_WITHDRAWN"
    with pytest.raises(DomainValidationError, match="current submitted"):
        withdrawn.withdraw(withdrawn_at=NOW + timedelta(hours=2))

    expiry_valid_until = NOW - timedelta(minutes=30)
    expired = _submit_simple_quote(
        valid_until=expiry_valid_until,
        submitted_at=NOW - timedelta(hours=1),
    )
    with pytest.raises(DomainValidationError, match="before valid_until"):
        expired.expire(expired_at=expiry_valid_until - timedelta(seconds=1))
    expired.expire(expired_at=expiry_valid_until)
    assert expired.status is QuoteStatus.EXPIRED
    assert expired.is_current is False
    assert expired.pending_events[-1].event_type == "QUOTE_EXPIRED"


def test_revision_after_validity_is_rejected() -> None:
    first = _quote()
    with pytest.raises(DomainValidationError, match="expired quote"):
        Quote.revise(
            first,
            aircraft_id=first.aircraft_id,
            base_price=first.base_price,
            price_components=first.price_components,
            repositioning_cost=first.repositioning_cost,
            inclusions=first.inclusions,
            exclusions=first.exclusions,
            cancellation_terms=first.cancellation_terms,
            payment_terms=first.payment_terms,
            valid_until=VALID_UNTIL + timedelta(days=1),
            submitted_at=VALID_UNTIL,
        )
