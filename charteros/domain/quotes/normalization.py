from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from charteros.domain.quotes.model import (
    PriceComponentApplicability,
    PriceComponentCategory,
    Quote,
    QuoteId,
)
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money

NORMALIZATION_VERSION = "v1"


class PricingConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class NormalizedFeeCategory(StrEnum):
    REPOSITIONING = "repositioning"
    FUEL_SURCHARGE = "fuel_surcharge"
    AIRPORT_FEES = "airport_fees"
    HANDLING = "handling"
    PARKING = "parking"
    CREW_OVERNIGHT = "crew_overnight"
    CATERING = "catering"
    DEICING = "deicing"
    PERMITS = "permits"
    TAXES = "taxes"
    BROKER_SERVICE_FEE = "broker_service_fee"
    OTHER = "other"


class UnresolvedComponentReason(StrEnum):
    EXCLUDED_WITHOUT_PRICE = "excluded_without_price"


class NormalizationCaveatCode(StrEnum):
    CONDITIONAL_FEES = "conditional_fees"
    UNPRICED_EXCLUSIONS = "unpriced_exclusions"
    OPERATOR_DEFINED_OTHER = "operator_defined_other"


@dataclass(frozen=True, slots=True)
class NormalizedFee:
    category: NormalizedFeeCategory
    label: str
    amount: Money
    condition: str | None


@dataclass(frozen=True, slots=True)
class UnresolvedPricingComponent:
    label: str
    reason: UnresolvedComponentReason


@dataclass(frozen=True, slots=True)
class NormalizationCaveat:
    code: NormalizationCaveatCode
    message: str


@dataclass(frozen=True, slots=True)
class QuoteNormalization:
    normalization_version: str
    quote_id: QuoteId
    quote_revision_number: int
    currency: Currency
    base_price: Money
    known_fees: tuple[NormalizedFee, ...]
    conditional_fees: tuple[NormalizedFee, ...]
    excluded_fees: tuple[str, ...]
    expected_total: Money
    worst_case_total: Money
    totals_complete: bool
    confidence: PricingConfidence
    caveats: tuple[NormalizationCaveat, ...]
    unresolved_components: tuple[UnresolvedPricingComponent, ...]


def _sum_fees(seed: Money, fees: tuple[NormalizedFee, ...]) -> Money:
    total = seed
    for fee in fees:
        total = total + fee.amount
    return total


def _normalized_category(category: PriceComponentCategory) -> NormalizedFeeCategory:
    return NormalizedFeeCategory(category.value)


def normalize_quote(quote: Quote) -> QuoteNormalization:
    """Normalize one canonical quote deterministically without FX or probabilistic estimates.

    Expected total is the base price plus all known fees. Worst-case total additionally assumes
    every explicitly priced conditional fee is triggered. Unpriced exclusions remain unresolved
    and are intentionally absent from both totals.
    """

    known: list[NormalizedFee] = []
    conditional: list[NormalizedFee] = []
    caveats: list[NormalizationCaveat] = []

    if quote.repositioning_cost is not None:
        known.append(
            NormalizedFee(
                category=NormalizedFeeCategory.REPOSITIONING,
                label="Repositioning",
                amount=quote.repositioning_cost,
                condition=None,
            )
        )

    has_operator_defined_other = False
    for component in quote.price_components:
        fee = NormalizedFee(
            category=_normalized_category(component.category),
            label=component.label,
            amount=component.amount,
            condition=component.condition,
        )
        if component.applicability is PriceComponentApplicability.CONDITIONAL:
            conditional.append(fee)
        else:
            known.append(fee)
        if component.category is PriceComponentCategory.OTHER:
            has_operator_defined_other = True

    if conditional:
        caveats.append(
            NormalizationCaveat(
                code=NormalizationCaveatCode.CONDITIONAL_FEES,
                message=(
                    "Expected total excludes conditional fees; worst-case total assumes every "
                    "explicitly priced conditional fee is triggered."
                ),
            )
        )

    unresolved = tuple(
        UnresolvedPricingComponent(
            label=label,
            reason=UnresolvedComponentReason.EXCLUDED_WITHOUT_PRICE,
        )
        for label in quote.exclusions
    )
    if unresolved:
        caveats.append(
            NormalizationCaveat(
                code=NormalizationCaveatCode.UNPRICED_EXCLUSIONS,
                message=(
                    "Unpriced exclusions are unresolved and omitted from expected and worst-case "
                    "totals; worst-case total is therefore not a complete all-in upper bound."
                ),
            )
        )

    if has_operator_defined_other:
        caveats.append(
            NormalizationCaveat(
                code=NormalizationCaveatCode.OPERATOR_DEFINED_OTHER,
                message=(
                    "At least one priced component uses the operator-defined 'other' category; "
                    "its amount is included but its commercial semantics are not standardized."
                ),
            )
        )

    known_fees = tuple(known)
    conditional_fees = tuple(conditional)
    expected_total = _sum_fees(quote.base_price, known_fees)
    worst_case_total = _sum_fees(expected_total, conditional_fees)

    if unresolved:
        confidence = PricingConfidence.LOW
    elif conditional or has_operator_defined_other:
        confidence = PricingConfidence.MEDIUM
    else:
        confidence = PricingConfidence.HIGH

    return QuoteNormalization(
        normalization_version=NORMALIZATION_VERSION,
        quote_id=quote.id,
        quote_revision_number=quote.revision_number,
        currency=quote.currency,
        base_price=quote.base_price,
        known_fees=known_fees,
        conditional_fees=conditional_fees,
        excluded_fees=quote.exclusions,
        expected_total=expected_total,
        worst_case_total=worst_case_total,
        totals_complete=not unresolved,
        confidence=confidence,
        caveats=tuple(caveats),
        unresolved_components=unresolved,
    )
