from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from charteros.domain.aircraft import AircraftId
from charteros.domain.quotes import (
    PriceComponent,
    PriceComponentApplicability,
    PriceComponentCategory,
    Quote,
    QuoteId,
    QuoteStatus,
)
from charteros.domain.quotes.normalization import (
    NormalizationCaveatCode,
    NormalizedFeeCategory,
    PricingConfidence,
    normalize_quote,
)
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError, MoneyOverflowError
from charteros.domain.shared.money import Money

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
EUR = Currency("EUR")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _quote(
    *,
    base_minor: int = 7_400_000,
    repositioning_minor: int | None = 300_000,
    components: tuple[PriceComponent, ...] = (),
    exclusions: tuple[str, ...] = (),
) -> Quote:
    return Quote(
        QuoteId(_id(3)),
        rfq_id=RfqId(_id(1)),
        aircraft_id=AircraftId(_id(2)),
        base_price=Money(base_minor, EUR),
        price_components=components,
        repositioning_cost=(
            Money(repositioning_minor, EUR) if repositioning_minor is not None else None
        ),
        inclusions=(),
        exclusions=exclusions,
        cancellation_terms=None,
        payment_terms=None,
        valid_until=NOW + timedelta(days=2),
        status=QuoteStatus.SUBMITTED,
        revision_number=1,
        supersedes_quote_id=None,
        submitted_at=NOW,
        is_current=True,
    )


def test_normalization_partitions_fees_and_surfaces_unresolved_exclusions() -> None:
    quote = _quote(
        components=(
            PriceComponent(
                category=PriceComponentCategory.HANDLING,
                label="Handling",
                amount=Money(125_000, EUR),
            ),
            PriceComponent(
                category=PriceComponentCategory.DEICING,
                label="Deicing",
                amount=Money(80_000, EUR),
                applicability=PriceComponentApplicability.CONDITIONAL,
                condition="Only if required before departure",
            ),
            PriceComponent(
                category=PriceComponentCategory.OTHER,
                label="Local authority charge",
                amount=Money(50_000, EUR),
            ),
        ),
        exclusions=("Crew overnight",),
    )

    result = normalize_quote(quote)

    assert result.normalization_version == "v1"
    assert result.base_price == Money(7_400_000, EUR)
    assert [fee.category for fee in result.known_fees] == [
        NormalizedFeeCategory.REPOSITIONING,
        NormalizedFeeCategory.HANDLING,
        NormalizedFeeCategory.OTHER,
    ]
    assert [fee.category for fee in result.conditional_fees] == [
        NormalizedFeeCategory.DEICING
    ]
    assert result.expected_total == Money(7_875_000, EUR)
    assert result.worst_case_total == Money(7_955_000, EUR)
    assert result.excluded_fees == ("Crew overnight",)
    assert result.totals_complete is False
    assert result.confidence is PricingConfidence.LOW
    assert [item.code for item in result.caveats] == [
        NormalizationCaveatCode.CONDITIONAL_FEES,
        NormalizationCaveatCode.UNPRICED_EXCLUSIONS,
        NormalizationCaveatCode.OPERATOR_DEFINED_OTHER,
    ]
    assert result.unresolved_components[0].label == "Crew overnight"
    assert result.unresolved_components[0].reason == "excluded_without_price"


def test_high_confidence_requires_fully_known_standardized_pricing() -> None:
    quote = _quote(
        repositioning_minor=None,
        components=(
            PriceComponent(
                category=PriceComponentCategory.AIRPORT_FEES,
                label="Airport fees",
                amount=Money(100_000, EUR),
            ),
        ),
    )

    result = normalize_quote(quote)

    assert result.expected_total == Money(7_500_000, EUR)
    assert result.worst_case_total == Money(7_500_000, EUR)
    assert result.totals_complete is True
    assert result.confidence is PricingConfidence.HIGH
    assert result.caveats == ()
    assert result.unresolved_components == ()


def test_priced_conditional_fee_is_medium_confidence_but_bounded() -> None:
    quote = _quote(
        repositioning_minor=None,
        components=(
            PriceComponent(
                category=PriceComponentCategory.CREW_OVERNIGHT,
                label="Crew overnight",
                amount=Money(90_000, EUR),
                applicability=PriceComponentApplicability.CONDITIONAL,
                condition="Only if duty-time extension requires overnight rest",
            ),
        ),
    )

    result = normalize_quote(quote)

    assert result.expected_total == Money(7_400_000, EUR)
    assert result.worst_case_total == Money(7_490_000, EUR)
    assert result.totals_complete is True
    assert result.confidence is PricingConfidence.MEDIUM


def test_descriptive_condition_does_not_make_known_fee_conditional() -> None:
    component = PriceComponent(
        category=PriceComponentCategory.TAXES,
        label="Taxes",
        amount=Money(50_000, EUR),
        condition="Known taxes at submission",
    )
    assert component.applicability is PriceComponentApplicability.KNOWN

    result = normalize_quote(
        _quote(
            repositioning_minor=None,
            components=(component,),
        )
    )
    assert len(result.known_fees) == 1
    assert result.conditional_fees == ()
    assert result.expected_total == result.worst_case_total


def test_conditional_fee_requires_explicit_condition_text() -> None:
    with pytest.raises(DomainValidationError, match="requires a condition"):
        PriceComponent(
            category=PriceComponentCategory.DEICING,
            label="Deicing",
            amount=Money(10_000, EUR),
            applicability=PriceComponentApplicability.CONDITIONAL,
        )


def test_normalization_preserves_money_overflow_failure() -> None:
    quote = _quote(
        base_minor=2**63 - 1,
        repositioning_minor=None,
        components=(
            PriceComponent(
                category=PriceComponentCategory.HANDLING,
                label="Handling",
                amount=Money(1, EUR),
            ),
        ),
    )
    with pytest.raises(MoneyOverflowError):
        normalize_quote(quote)


@pytest.mark.property
@given(
    base_minor=st.integers(min_value=1, max_value=1_000_000_000),
    repositioning_minor=st.one_of(
        st.none(),
        st.integers(min_value=0, max_value=100_000_000),
    ),
    known_amounts=st.lists(
        st.integers(min_value=0, max_value=100_000_000),
        max_size=8,
    ),
    conditional_amounts=st.lists(
        st.integers(min_value=0, max_value=100_000_000),
        max_size=8,
    ),
)
def test_normalization_totals_are_deterministic_partition_sums(
    base_minor: int,
    repositioning_minor: int | None,
    known_amounts: list[int],
    conditional_amounts: list[int],
) -> None:
    known_components = tuple(
        PriceComponent(
            category=PriceComponentCategory.HANDLING,
            label=f"Known {index}",
            amount=Money(amount, EUR),
        )
        for index, amount in enumerate(known_amounts)
    )
    conditional_components = tuple(
        PriceComponent(
            category=PriceComponentCategory.DEICING,
            label=f"Conditional {index}",
            amount=Money(amount, EUR),
            applicability=PriceComponentApplicability.CONDITIONAL,
            condition=f"Condition {index}",
        )
        for index, amount in enumerate(conditional_amounts)
    )
    quote = _quote(
        base_minor=base_minor,
        repositioning_minor=repositioning_minor,
        components=known_components + conditional_components,
    )

    first = normalize_quote(quote)
    second = normalize_quote(quote)

    expected_minor = base_minor + sum(known_amounts)
    if repositioning_minor is not None:
        expected_minor += repositioning_minor
    worst_case_minor = expected_minor + sum(conditional_amounts)

    assert first == second
    assert first.expected_total == Money(expected_minor, EUR)
    assert first.worst_case_total == Money(worst_case_minor, EUR)
    assert first.expected_total <= first.worst_case_total
    assert len(first.known_fees) == len(known_amounts) + (
        1 if repositioning_minor is not None else 0
    )
    assert len(first.conditional_fees) == len(conditional_amounts)
