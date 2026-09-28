from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.comparison import (
    MAX_COMPARISON_POINTS,
    ComparisonDraft,
    score_comparison_drafts,
)
from charteros.domain.quotes.normalization import PricingConfidence
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money

EUR = Currency("EUR")
USD = Currency("USD")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _draft(
    value: int,
    *,
    currency: Currency = EUR,
    expected: int,
    worst: int,
    reposition: int,
    risk: int,
    confidence: PricingConfidence = PricingConfidence.HIGH,
    eligible: bool = True,
) -> ComparisonDraft:
    return ComparisonDraft(
        quote_id=QuoteId(_id(value)),
        expected_total=Money(expected, currency),
        worst_case_total=Money(worst, currency),
        reposition_distance_tenths_nm=reposition if eligible else None,
        schedule_risk_basis_points=risk if eligible else None,
        pricing_confidence=confidence,
        eligible=eligible,
    )


def test_score_decomposition_is_explicit_and_deterministic() -> None:
    cheaper = _draft(
        1,
        expected=7_000_000,
        worst=7_400_000,
        reposition=100,
        risk=1_000,
    )
    expensive = _draft(
        2,
        expected=8_000_000,
        worst=8_500_000,
        reposition=500,
        risk=4_000,
        confidence=PricingConfidence.MEDIUM,
    )

    forward = score_comparison_drafts((expensive, cheaper))
    reverse = score_comparison_drafts((cheaper, expensive))

    assert forward == reverse
    assert [item.quote_id for item in forward] == [cheaper.quote_id, expensive.quote_id]
    assert forward[0].currency_rank == 1
    assert forward[0].score.total_basis_points == MAX_COMPARISON_POINTS
    assert forward[0].score.total_basis_points == sum(
        (
            forward[0].score.expected_total_points or 0,
            forward[0].score.worst_case_total_points or 0,
            forward[0].score.reposition_points or 0,
            forward[0].score.operational_risk_points or 0,
            forward[0].score.pricing_confidence_points,
        )
    )
    assert forward[1].score.total_basis_points == 500


def test_mixed_currencies_are_ranked_only_within_currency_cohorts() -> None:
    eur = _draft(
        1,
        expected=7_000_000,
        worst=7_000_000,
        reposition=100,
        risk=500,
    )
    usd = _draft(
        2,
        currency=USD,
        expected=6_000_000,
        worst=6_000_000,
        reposition=50,
        risk=250,
    )

    result = score_comparison_drafts((usd, eur))

    assert [(str(item.score.currency_scope), item.currency_rank) for item in result] == [
        ("EUR", 1),
        ("USD", 1),
    ]
    assert all(item.score.cohort_size == 1 for item in result)


def test_ineligible_quote_is_never_ranked_even_when_cheapest() -> None:
    ineligible = _draft(
        1,
        expected=1,
        worst=1,
        reposition=0,
        risk=0,
        eligible=False,
    )
    eligible = _draft(
        2,
        expected=9_000_000,
        worst=9_000_000,
        reposition=900,
        risk=8_000,
    )

    result = score_comparison_drafts((ineligible, eligible))
    by_id = {item.quote_id: item for item in result}

    assert by_id[eligible.quote_id].currency_rank == 1
    assert by_id[eligible.quote_id].score.total_basis_points == MAX_COMPARISON_POINTS
    assert by_id[ineligible.quote_id].currency_rank is None
    assert by_id[ineligible.quote_id].score.total_basis_points is None
    assert by_id[ineligible.quote_id].score.method == "ineligible_unscored_v1"


def test_same_metrics_use_stable_quote_id_tie_break() -> None:
    first = _draft(
        1,
        expected=7_000_000,
        worst=7_000_000,
        reposition=100,
        risk=500,
    )
    second = _draft(
        2,
        expected=7_000_000,
        worst=7_000_000,
        reposition=100,
        risk=500,
    )

    result = score_comparison_drafts((second, first))
    assert [(item.quote_id, item.currency_rank) for item in result] == [
        (first.quote_id, 1),
        (second.quote_id, 2),
    ]


@pytest.mark.property
@given(
    low=st.integers(min_value=1, max_value=1_000_000_000),
    delta=st.integers(min_value=0, max_value=1_000_000_000),
)
def test_lower_price_never_scores_worse_when_other_dimensions_match(
    low: int,
    delta: int,
) -> None:
    high = low + delta
    if high > 2**63 - 1:
        return
    cheaper = _draft(
        1,
        expected=low,
        worst=low,
        reposition=100,
        risk=500,
    )
    expensive = _draft(
        2,
        expected=high,
        worst=high,
        reposition=100,
        risk=500,
    )

    result = score_comparison_drafts((expensive, cheaper))
    by_id = {item.quote_id: item for item in result}
    assert (
        by_id[cheaper.quote_id].score.total_basis_points
        >= by_id[expensive.quote_id].score.total_basis_points
    )
