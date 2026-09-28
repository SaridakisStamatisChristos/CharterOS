from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.aircraft import AircraftId
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money


class QuoteId(TypedId):
    __slots__ = ()


class QuoteStatus(StrEnum):
    SUBMITTED = "submitted"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class PriceComponentCategory(StrEnum):
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


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


def _canonical_items(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if len(values) > 128:
        raise DomainValidationError(f"{field_name} cannot contain more than 128 items")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _canonical_text(value, field_name=field_name, max_length=500)
        if item is None:
            raise DomainValidationError(f"{field_name} cannot contain blank items")
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(item)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class PriceComponent:
    category: PriceComponentCategory
    label: str
    amount: Money
    condition: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, PriceComponentCategory):
            object.__setattr__(self, "category", PriceComponentCategory(self.category))
        label = _canonical_text(self.label, field_name="price component label", max_length=200)
        if label is None:
            raise DomainValidationError("price component label is required")
        if self.amount.amount_minor < 0:
            raise DomainValidationError("price component amount cannot be negative")
        condition = _canonical_text(
            self.condition,
            field_name="price component condition",
            max_length=500,
        )
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "condition", condition)


class Quote(AggregateRoot[QuoteId]):
    aggregate_type = "quote"

    def __init__(
        self,
        quote_id: QuoteId,
        *,
        rfq_id: RfqId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        status: QuoteStatus,
        revision_number: int,
        supersedes_quote_id: QuoteId | None,
        submitted_at: datetime,
        is_current: bool,
        expired_at: datetime | None = None,
        withdrawn_at: datetime | None = None,
        superseded_at: datetime | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(quote_id, version=version)
        if not isinstance(base_price, Money):
            raise DomainValidationError("base_price must be Money")
        if base_price.amount_minor <= 0:
            raise DomainValidationError("base_price must be positive")
        if len(price_components) > 128:
            raise DomainValidationError("price_components cannot contain more than 128 items")
        if repositioning_cost is not None and repositioning_cost.amount_minor < 0:
            raise DomainValidationError("repositioning_cost cannot be negative")
        if not isinstance(revision_number, int) or isinstance(revision_number, bool):
            raise DomainValidationError("revision_number must be an integer")
        if revision_number < 1:
            raise DomainValidationError("revision_number must be positive")
        if revision_number == 1 and supersedes_quote_id is not None:
            raise DomainValidationError("initial quote cannot supersede another quote")
        if revision_number > 1 and supersedes_quote_id is None:
            raise DomainValidationError("revised quote must identify the superseded quote")

        submitted = _utc(submitted_at, field_name="submitted_at")
        valid = _utc(valid_until, field_name="valid_until")
        if valid <= submitted:
            raise DomainValidationError("valid_until must be after submitted_at")

        components = tuple(price_components)
        currency = base_price.currency
        if repositioning_cost is not None and repositioning_cost.currency != currency:
            raise DomainValidationError("repositioning_cost currency must match quote currency")
        for component in components:
            if component.amount.currency != currency:
                raise DomainValidationError(
                    "all price component currencies must match quote currency"
                )

        canonical_inclusions = _canonical_items(inclusions, field_name="inclusions")
        canonical_exclusions = _canonical_items(exclusions, field_name="exclusions")
        overlap = {item.casefold() for item in canonical_inclusions} & {
            item.casefold() for item in canonical_exclusions
        }
        if overlap:
            raise DomainValidationError("the same commercial item cannot be included and excluded")

        self.rfq_id = rfq_id
        self.aircraft_id = aircraft_id
        self.base_price = base_price
        self.price_components = components
        self.repositioning_cost = repositioning_cost
        self.inclusions = canonical_inclusions
        self.exclusions = canonical_exclusions
        self.cancellation_terms = _canonical_text(
            cancellation_terms,
            field_name="cancellation_terms",
            max_length=4000,
        )
        self.payment_terms = _canonical_text(
            payment_terms,
            field_name="payment_terms",
            max_length=4000,
        )
        self.valid_until = valid
        self.status = QuoteStatus(status)
        self.revision_number = revision_number
        self.supersedes_quote_id = supersedes_quote_id
        self.submitted_at = submitted
        self.is_current = is_current
        self.expired_at = (
            _utc(expired_at, field_name="expired_at") if expired_at is not None else None
        )
        self.withdrawn_at = (
            _utc(withdrawn_at, field_name="withdrawn_at") if withdrawn_at is not None else None
        )
        self.superseded_at = (
            _utc(superseded_at, field_name="superseded_at")
            if superseded_at is not None
            else None
        )
        self._validate_state()

    @property
    def currency(self) -> Currency:
        return self.base_price.currency

    @property
    def submitted_total(self) -> Money:
        total = self.base_price
        if self.repositioning_cost is not None:
            total = total + self.repositioning_cost
        for component in self.price_components:
            total = total + component.amount
        return total

    def _validate_state(self) -> None:
        terminal = (self.expired_at, self.withdrawn_at, self.superseded_at)
        if self.status is QuoteStatus.SUBMITTED:
            if not self.is_current or any(value is not None for value in terminal):
                raise DomainValidationError("submitted quote has inconsistent lifecycle state")
            return
        if self.is_current:
            raise DomainValidationError("terminal quote cannot be current")
        if self.status is QuoteStatus.EXPIRED:
            if (
                self.expired_at is None
                or self.withdrawn_at is not None
                or self.superseded_at is not None
            ):
                raise DomainValidationError("expired quote has inconsistent lifecycle timestamps")
            if self.expired_at < self.valid_until:
                raise DomainValidationError("expired_at cannot precede valid_until")
        elif self.status is QuoteStatus.WITHDRAWN:
            if (
                self.withdrawn_at is None
                or self.expired_at is not None
                or self.superseded_at is not None
            ):
                raise DomainValidationError("withdrawn quote has inconsistent lifecycle timestamps")
            if self.withdrawn_at < self.submitted_at:
                raise DomainValidationError("withdrawn_at cannot precede submitted_at")
        elif self.status is QuoteStatus.SUPERSEDED:
            if (
                self.superseded_at is None
                or self.expired_at is not None
                or self.withdrawn_at is not None
            ):
                raise DomainValidationError(
                    "superseded quote has inconsistent lifecycle timestamps"
                )
            if self.superseded_at < self.submitted_at:
                raise DomainValidationError("superseded_at cannot precede submitted_at")

    @classmethod
    def submit(
        cls,
        *,
        rfq_id: RfqId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        submitted_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Quote:
        quote = cls(
            QuoteId.new(),
            rfq_id=rfq_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid_until,
            status=QuoteStatus.SUBMITTED,
            revision_number=1,
            supersedes_quote_id=None,
            submitted_at=submitted_at,
            is_current=True,
        )
        quote._record_event(
            "QUOTE_SUBMITTED",
            quote._event_payload(),
            correlation_id=correlation_id,
            occurred_at=quote.submitted_at,
        )
        return quote

    @classmethod
    def revise(
        cls,
        previous: Quote,
        *,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        submitted_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Quote:
        if previous.status is not QuoteStatus.SUBMITTED or not previous.is_current:
            raise DomainValidationError("only the current submitted quote can be revised")
        when = _utc(submitted_at, field_name="submitted_at")
        if when >= previous.valid_until:
            raise DomainValidationError("expired quote cannot be revised")
        quote = cls(
            QuoteId.new(),
            rfq_id=previous.rfq_id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid_until,
            status=QuoteStatus.SUBMITTED,
            revision_number=previous.revision_number + 1,
            supersedes_quote_id=previous.id,
            submitted_at=when,
            is_current=True,
        )
        quote._record_event(
            "QUOTE_REVISED",
            quote._event_payload(),
            correlation_id=correlation_id,
            occurred_at=when,
        )
        return quote

    def supersede(
        self,
        *,
        superseded_at: datetime,
        replacement_quote_id: QuoteId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not QuoteStatus.SUBMITTED or not self.is_current:
            raise DomainValidationError("only the current submitted quote can be superseded")
        when = _utc(superseded_at, field_name="superseded_at")
        if when < self.submitted_at:
            raise DomainValidationError("superseded_at cannot precede submitted_at")
        if when >= self.valid_until:
            raise DomainValidationError("expired quote cannot be superseded")
        self.status = QuoteStatus.SUPERSEDED
        self.is_current = False
        self.superseded_at = when
        self._record_event(
            "QUOTE_SUPERSEDED",
            {"replacement_quote_id": str(replacement_quote_id)},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def withdraw(
        self,
        *,
        withdrawn_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not QuoteStatus.SUBMITTED or not self.is_current:
            raise DomainValidationError("only the current submitted quote can be withdrawn")
        when = _utc(withdrawn_at, field_name="withdrawn_at")
        if when < self.submitted_at:
            raise DomainValidationError("withdrawn_at cannot precede submitted_at")
        if when >= self.valid_until:
            raise DomainValidationError("expired quote cannot be withdrawn; expire it instead")
        self.status = QuoteStatus.WITHDRAWN
        self.is_current = False
        self.withdrawn_at = when
        self._record_event(
            "QUOTE_WITHDRAWN",
            {"withdrawn_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def expire(
        self,
        *,
        expired_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not QuoteStatus.SUBMITTED or not self.is_current:
            raise DomainValidationError("only the current submitted quote can expire")
        when = _utc(expired_at, field_name="expired_at")
        if when < self.valid_until:
            raise DomainValidationError("quote cannot expire before valid_until")
        self.status = QuoteStatus.EXPIRED
        self.is_current = False
        self.expired_at = when
        self._record_event(
            "QUOTE_EXPIRED",
            {"expired_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def _event_payload(self) -> dict[str, object]:
        return {
            "rfq_id": str(self.rfq_id),
            "aircraft_id": str(self.aircraft_id),
            "currency": str(self.currency),
            "base_amount_minor": self.base_price.amount_minor,
            "repositioning_amount_minor": (
                self.repositioning_cost.amount_minor
                if self.repositioning_cost is not None
                else None
            ),
            "price_components": [
                {
                    "category": component.category.value,
                    "label": component.label,
                    "amount_minor": component.amount.amount_minor,
                    "condition": component.condition,
                }
                for component in self.price_components
            ],
            "submitted_total_minor": self.submitted_total.amount_minor,
            "valid_until": self.valid_until.isoformat().replace("+00:00", "Z"),
            "revision_number": self.revision_number,
            "supersedes_quote_id": (
                str(self.supersedes_quote_id) if self.supersedes_quote_id is not None else None
            ),
        }
