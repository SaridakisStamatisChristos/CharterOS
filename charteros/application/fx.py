from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import DomainEventRepository
from charteros.application.ports.fx import FxLockRepository, FxRateRepository
from charteros.application.quote_comparison import MissionQuoteComparison, QuoteComparisonEntry
from charteros.domain.fx import (
    CONVERSION_POLICY_VERSION,
    LOCK_POLICY_VERSION,
    ROUNDING_POLICY_VERSION,
    FxConversion,
    FxLock,
    FxLockedQuote,
    FxRateId,
    FxRateObservation,
    convert_money,
    identity_conversion,
)
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.comparison import ComparisonDraft, score_comparison_drafts
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


@dataclass(frozen=True, slots=True)
class FxLockSummary:
    lock: FxLock
    global_rank_by_quote: dict[QuoteId, int]


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_source(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainValidationError("fx_source cannot be blank")
    if len(normalized) > 64:
        raise DomainValidationError("fx_source cannot exceed 64 characters")
    return normalized


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fx_lock_digest(
    *,
    buyer_id: OrganizationId,
    mission_id: MissionId,
    base_currency: Currency,
    fx_source: str,
    locked_at: datetime,
    entries: tuple[FxLockedQuote, ...],
) -> str:
    payload = {
        "lock_policy_version": LOCK_POLICY_VERSION,
        "conversion_policy_version": CONVERSION_POLICY_VERSION,
        "rounding_policy": ROUNDING_POLICY_VERSION,
        "buyer_id": str(buyer_id),
        "mission_id": str(mission_id),
        "base_currency": str(base_currency),
        "fx_source": fx_source,
        "locked_at": _format_datetime(locked_at),
        "quotes": [
            {
                "quote_id": str(entry.quote_id),
                "quote_revision_number": entry.quote_revision_number,
                "original_currency": str(entry.original_expected.currency),
                "original_expected_minor": entry.original_expected.amount_minor,
                "original_worst_case_minor": entry.original_worst_case.amount_minor,
                "converted_currency": str(entry.converted_expected.currency),
                "converted_expected_minor": entry.converted_expected.amount_minor,
                "converted_worst_case_minor": entry.converted_worst_case.amount_minor,
                "rate_id": str(entry.rate_id) if entry.rate_id is not None else None,
                "rate": entry.rate_text,
                "fx_source": entry.fx_source,
                "fx_source_version": entry.fx_source_version,
                "fx_timestamp": _format_datetime(entry.fx_timestamp),
                "rate_recorded_at": _format_datetime(entry.rate_recorded_at),
                "source_minor_exponent": entry.source_minor_exponent,
                "target_minor_exponent": entry.target_minor_exponent,
                "global_rank": entry.global_rank,
                "global_score_method": entry.global_score_method,
                "global_score_total_basis_points": entry.global_score_total_basis_points,
            }
            for entry in sorted(entries, key=lambda item: item.quote_id.value.hex)
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FxService:
    def __init__(
        self,
        *,
        rates: FxRateRepository,
        locks: FxLockRepository,
        events: DomainEventRepository,
    ) -> None:
        self._rates = rates
        self._locks = locks
        self._events = events

    def record_rate(
        self,
        *,
        source_currency: Currency,
        target_currency: Currency,
        rate_text: str,
        source_minor_exponent: int,
        target_minor_exponent: int,
        fx_source: str,
        fx_source_version: str,
        fx_timestamp: datetime,
        recorded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> FxRateObservation:
        rate = FxRateObservation.record(
            source_currency=source_currency,
            target_currency=target_currency,
            rate_text=rate_text,
            source_minor_exponent=source_minor_exponent,
            target_minor_exponent=target_minor_exponent,
            fx_source=fx_source,
            fx_source_version=fx_source_version,
            fx_timestamp=fx_timestamp,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._rates.add(rate)
        self._events.add_aggregate_events(rate)
        return rate

    def correct_rate(
        self,
        *,
        rate_id: FxRateId,
        rate_text: str,
        fx_source_version: str,
        recorded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> FxRateObservation:
        previous = self._rates.get_for_update(rate_id)
        if previous is None:
            raise EntityNotFoundError("FX rate observation does not exist")
        if self._rates.find_successor(rate_id) is not None:
            raise EntityConflictError("FX rate observation has already been corrected")
        correction = FxRateObservation.correction(
            previous=previous,
            rate_text=rate_text,
            fx_source_version=fx_source_version,
            recorded_at=recorded_at,
            correlation_id=correlation_id,
        )
        self._rates.add(correction)
        self._events.add_aggregate_events(correction)
        return correction

    def lock_comparison(
        self,
        *,
        comparison: MissionQuoteComparison,
        buyer_id: OrganizationId,
        base_currency: Currency,
        fx_source: str,
        locked_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> FxLockSummary:
        when = _utc(locked_at, field_name="locked_at")
        source_name = _normalized_source(fx_source)
        eligible = [entry for entry in comparison.entries if entry.decision_eligible]
        if not eligible:
            raise EntityConflictError("cannot create FX lock without an eligible quote")

        rates_by_currency: dict[Currency, FxRateObservation] = {}
        for currency in sorted({entry.normalization.currency for entry in eligible}):
            if currency == base_currency:
                continue
            rate = self._rates.latest_for_pair(
                source_currency=currency,
                target_currency=base_currency,
                fx_source=source_name,
                known_as_of=when,
            )
            if rate is None:
                raise EntityConflictError(
                    f"missing explicit FX evidence for {currency}->{base_currency} "
                    f"from source {source_name}"
                )
            rates_by_currency[currency] = rate

        converted: dict[QuoteId, tuple[FxConversion, FxConversion, QuoteComparisonEntry]] = {}
        global_drafts: list[ComparisonDraft] = []
        for entry in eligible:
            normalization = entry.normalization
            rate = rates_by_currency.get(normalization.currency)
            if rate is None:
                expected_conversion = identity_conversion(
                    amount=normalization.expected_total,
                    at=when,
                )
                worst_conversion = identity_conversion(
                    amount=normalization.worst_case_total,
                    at=when,
                )
            else:
                expected_conversion = convert_money(
                    amount=normalization.expected_total,
                    rate=rate,
                    base_currency=base_currency,
                )
                worst_conversion = convert_money(
                    amount=normalization.worst_case_total,
                    rate=rate,
                    base_currency=base_currency,
                )

            suitability = entry.aircraft_suitability
            if (
                suitability.reposition_distance_tenths_nm is None
                or suitability.schedule_risk_basis_points is None
            ):
                raise EntityConflictError(
                    "eligible quote is missing operational evidence required for global ranking"
                )
            global_drafts.append(
                ComparisonDraft(
                    quote_id=entry.quote.id,
                    expected_total=expected_conversion.converted,
                    worst_case_total=worst_conversion.converted,
                    reposition_distance_tenths_nm=suitability.reposition_distance_tenths_nm,
                    schedule_risk_basis_points=suitability.schedule_risk_basis_points,
                    pricing_confidence=normalization.confidence,
                    eligible=True,
                )
            )
            converted[entry.quote.id] = (
                expected_conversion,
                worst_conversion,
                entry,
            )

        scored = score_comparison_drafts(tuple(global_drafts))
        scored_by_id = {item.quote_id: item for item in scored}

        lock_entries: list[FxLockedQuote] = []
        for quote_id, item in converted.items():
            expected, worst, entry = item
            score = scored_by_id.get(quote_id)
            if score is None or score.currency_rank is None or score.score.total_basis_points is None:
                raise EntityConflictError("global FX ranking did not produce a complete rank")
            if expected.rate_id != worst.rate_id or expected.rate_text != worst.rate_text:
                raise EntityConflictError("FX conversion evidence diverged within one quote")
            lock_entries.append(
                FxLockedQuote(
                    quote_id=quote_id,
                    quote_revision_number=entry.quote.revision_number,
                    original_expected=entry.normalization.expected_total,
                    original_worst_case=entry.normalization.worst_case_total,
                    converted_expected=expected.converted,
                    converted_worst_case=worst.converted,
                    rate_id=expected.rate_id,
                    rate_text=expected.rate_text,
                    fx_source=expected.fx_source,
                    fx_source_version=expected.fx_source_version,
                    fx_timestamp=expected.fx_timestamp,
                    rate_recorded_at=expected.rate_recorded_at,
                    source_minor_exponent=expected.source_minor_exponent,
                    target_minor_exponent=expected.target_minor_exponent,
                    global_rank=score.currency_rank,
                    global_score_method="fx_global_" + score.score.method,
                    global_score_total_basis_points=score.score.total_basis_points,
                )
            )

        ordered_entries = tuple(
            sorted(lock_entries, key=lambda entry: (entry.global_rank, entry.quote_id.value.hex))
        )
        digest = fx_lock_digest(
            buyer_id=buyer_id,
            mission_id=comparison.mission_id,
            base_currency=base_currency,
            fx_source=source_name,
            locked_at=when,
            entries=ordered_entries,
        )
        lock = FxLock.create(
            buyer_id=buyer_id,
            mission_id=comparison.mission_id,
            base_currency=base_currency,
            fx_source=source_name,
            locked_at=when,
            entries=ordered_entries,
            integrity_digest=digest,
            correlation_id=correlation_id,
        )
        self._locks.add(lock)
        self._events.add_aggregate_events(lock)
        return FxLockSummary(
            lock=lock,
            global_rank_by_quote={
                entry.quote_id: entry.global_rank for entry in ordered_entries
            },
        )
