from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.normalization import PricingConfidence
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.money import Money

COMPARISON_POLICY_VERSION = "quote-comparison-v1"
EXPECTED_TOTAL_WEIGHT = 2_500
WORST_CASE_TOTAL_WEIGHT = 2_500
REPOSITION_WEIGHT = 2_000
OPERATIONAL_RISK_WEIGHT = 2_000
PRICING_CONFIDENCE_WEIGHT = 1_000
MAX_COMPARISON_POINTS = (
    EXPECTED_TOTAL_WEIGHT
    + WORST_CASE_TOTAL_WEIGHT
    + REPOSITION_WEIGHT
    + OPERATIONAL_RISK_WEIGHT
    + PRICING_CONFIDENCE_WEIGHT
)


@dataclass(frozen=True, slots=True)
class ComparisonDraft:
    quote_id: QuoteId
    expected_total: Money
    worst_case_total: Money
    reposition_distance_tenths_nm: int | None
    schedule_risk_basis_points: int | None
    pricing_confidence: PricingConfidence
    eligible: bool

    def __post_init__(self) -> None:
        if self.expected_total.currency != self.worst_case_total.currency:
            raise DomainValidationError("comparison draft totals must use the same currency")
        if self.eligible and (
            self.reposition_distance_tenths_nm is None
            or self.schedule_risk_basis_points is None
        ):
            raise DomainValidationError(
                "eligible comparison draft requires reposition and operational risk metrics"
            )
        if self.reposition_distance_tenths_nm is not None and self.reposition_distance_tenths_nm < 0:
            raise DomainValidationError("reposition distance cannot be negative")
        if self.schedule_risk_basis_points is not None and self.schedule_risk_basis_points < 0:
            raise DomainValidationError("schedule risk cannot be negative")

    @property
    def currency(self) -> Currency:
        return self.expected_total.currency


@dataclass(frozen=True, slots=True)
class ComparisonScoreDecomposition:
    method: str
    total_basis_points: int | None
    expected_total_points: int | None
    worst_case_total_points: int | None
    reposition_points: int | None
    operational_risk_points: int | None
    pricing_confidence_points: int
    currency_scope: Currency
    cohort_size: int


@dataclass(frozen=True, slots=True)
class ScoredComparison:
    quote_id: QuoteId
    currency_rank: int | None
    score: ComparisonScoreDecomposition


def _relative_points(value: int, values: tuple[int, ...], *, weight: int) -> int:
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return weight
    points = (
        Decimal(weight)
        * Decimal(maximum - value)
        / Decimal(maximum - minimum)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(points)


def _confidence_points(confidence: PricingConfidence) -> int:
    if confidence is PricingConfidence.HIGH:
        return PRICING_CONFIDENCE_WEIGHT
    if confidence is PricingConfidence.MEDIUM:
        return PRICING_CONFIDENCE_WEIGHT // 2
    return 0


def score_comparison_drafts(
    drafts: tuple[ComparisonDraft, ...],
) -> tuple[ScoredComparison, ...]:
    """Score eligible quotes only within same-currency cohorts.

    Ineligible quotes intentionally receive no aggregate score/rank. This prevents an infeasible
    aircraft or commercially invalid quote from becoming attractive through low price alone.
    """

    by_currency: dict[Currency, list[ComparisonDraft]] = {}
    for draft in drafts:
        by_currency.setdefault(draft.currency, []).append(draft)

    scored: list[ScoredComparison] = []
    for currency in sorted(by_currency):
        cohort = by_currency[currency]
        eligible = [draft for draft in cohort if draft.eligible]
        if eligible:
            expected_values = tuple(item.expected_total.amount_minor for item in eligible)
            worst_values = tuple(item.worst_case_total.amount_minor for item in eligible)
            reposition_values = tuple(
                item.reposition_distance_tenths_nm
                for item in eligible
                if item.reposition_distance_tenths_nm is not None
            )
            risk_values = tuple(
                item.schedule_risk_basis_points
                for item in eligible
                if item.schedule_risk_basis_points is not None
            )
            eligible_scored: list[ScoredComparison] = []
            for draft in eligible:
                assert draft.reposition_distance_tenths_nm is not None
                assert draft.schedule_risk_basis_points is not None
                expected_points = _relative_points(
                    draft.expected_total.amount_minor,
                    expected_values,
                    weight=EXPECTED_TOTAL_WEIGHT,
                )
                worst_points = _relative_points(
                    draft.worst_case_total.amount_minor,
                    worst_values,
                    weight=WORST_CASE_TOTAL_WEIGHT,
                )
                reposition_points = _relative_points(
                    draft.reposition_distance_tenths_nm,
                    reposition_values,
                    weight=REPOSITION_WEIGHT,
                )
                risk_points = _relative_points(
                    draft.schedule_risk_basis_points,
                    risk_values,
                    weight=OPERATIONAL_RISK_WEIGHT,
                )
                confidence_points = _confidence_points(draft.pricing_confidence)
                total = (
                    expected_points
                    + worst_points
                    + reposition_points
                    + risk_points
                    + confidence_points
                )
                eligible_scored.append(
                    ScoredComparison(
                        quote_id=draft.quote_id,
                        currency_rank=None,
                        score=ComparisonScoreDecomposition(
                            method="currency_cohort_minmax_v1",
                            total_basis_points=total,
                            expected_total_points=expected_points,
                            worst_case_total_points=worst_points,
                            reposition_points=reposition_points,
                            operational_risk_points=risk_points,
                            pricing_confidence_points=confidence_points,
                            currency_scope=currency,
                            cohort_size=len(eligible),
                        ),
                    )
                )

            draft_by_id = {draft.quote_id: draft for draft in eligible}
            eligible_scored.sort(
                key=lambda item: (
                    -(item.score.total_basis_points or 0),
                    draft_by_id[item.quote_id].expected_total.amount_minor,
                    draft_by_id[item.quote_id].worst_case_total.amount_minor,
                    draft_by_id[item.quote_id].reposition_distance_tenths_nm or 0,
                    item.quote_id.value.hex,
                )
            )
            for rank, item in enumerate(eligible_scored, start=1):
                scored.append(
                    ScoredComparison(
                        quote_id=item.quote_id,
                        currency_rank=rank,
                        score=item.score,
                    )
                )

        for draft in cohort:
            if draft.eligible:
                continue
            scored.append(
                ScoredComparison(
                    quote_id=draft.quote_id,
                    currency_rank=None,
                    score=ComparisonScoreDecomposition(
                        method="ineligible_unscored_v1",
                        total_basis_points=None,
                        expected_total_points=None,
                        worst_case_total_points=None,
                        reposition_points=None,
                        operational_risk_points=None,
                        pricing_confidence_points=_confidence_points(
                            draft.pricing_confidence
                        ),
                        currency_scope=currency,
                        cohort_size=len(eligible),
                    ),
                )
            )

    scored.sort(
        key=lambda item: (
            str(item.score.currency_scope),
            item.currency_rank is None,
            item.currency_rank or 0,
            item.quote_id.value.hex,
        )
    )
    return tuple(scored)
