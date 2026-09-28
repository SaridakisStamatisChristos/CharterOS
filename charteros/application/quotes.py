from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import AircraftRepository, DomainEventRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.domain.aircraft import AircraftId
from charteros.domain.quotes import PriceComponent, Quote, QuoteId, QuoteStatus
from charteros.domain.rfqs import RfqId, RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.shared.money import Money


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class QuoteService:
    def __init__(
        self,
        *,
        quotes: QuoteRepository,
        rfqs: RfqRepository,
        missions: MissionRepository,
        aircraft: AircraftRepository,
        events: DomainEventRepository,
    ) -> None:
        self._quotes = quotes
        self._rfqs = rfqs
        self._missions = missions
        self._aircraft = aircraft
        self._events = events

    def submit(
        self,
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
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        submitted_at = _utc(now, field_name="now")
        valid = _utc(valid_until, field_name="valid_until")
        rfq = self._rfqs.get_for_update(rfq_id)
        if rfq is None:
            raise EntityNotFoundError("RFQ does not exist")
        if rfq.status is not RfqStatus.ACKNOWLEDGED:
            raise EntityConflictError("quotes can only be submitted for acknowledged RFQs")
        assert rfq.response_deadline is not None
        if submitted_at >= rfq.response_deadline:
            raise EntityConflictError("RFQ response deadline has passed")
        if self._quotes.get_current_for_rfq(rfq_id) is not None:
            raise EntityConflictError("an authoritative quote already exists for this RFQ")

        mission = self._missions.get(rfq.mission_id)
        if mission is None:
            raise EntityNotFoundError("RFQ mission does not exist")
        self._validate_valid_until(
            valid_until=valid,
            now=submitted_at,
            mission_departure=mission.departure_window.start,
        )
        self._validate_aircraft(aircraft_id=aircraft_id, operator_id=rfq.operator_id)

        quote = Quote.submit(
            rfq_id=rfq.id,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid,
            submitted_at=submitted_at,
            correlation_id=correlation_id,
        )
        expected_rfq_version = rfq.version
        rfq.mark_quoted(
            quoted_at=submitted_at,
            quote_id=str(quote.id),
            correlation_id=correlation_id,
        )
        self._quotes.add(quote)
        self._rfqs.save(rfq, expected_version=expected_rfq_version)
        self._events.add_aggregate_events(quote)
        self._events.add_aggregate_events(rfq)
        return quote

    def revise(
        self,
        *,
        quote_id: QuoteId,
        aircraft_id: AircraftId,
        base_price: Money,
        price_components: tuple[PriceComponent, ...],
        repositioning_cost: Money | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        cancellation_terms: str | None,
        payment_terms: str | None,
        valid_until: datetime,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        submitted_at = _utc(now, field_name="now")
        valid = _utc(valid_until, field_name="valid_until")
        previous = self._get_for_update(quote_id)
        if previous.status is not QuoteStatus.SUBMITTED or not previous.is_current:
            raise EntityConflictError("only the current submitted quote can be revised")
        if submitted_at >= previous.valid_until:
            raise EntityConflictError("expired quote cannot be revised")

        rfq = self._rfqs.get(previous.rfq_id)
        if rfq is None:
            raise EntityNotFoundError("quote RFQ does not exist")
        if rfq.status is not RfqStatus.QUOTED:
            raise EntityConflictError("quote RFQ is not in quoted state")
        mission = self._missions.get(rfq.mission_id)
        if mission is None:
            raise EntityNotFoundError("RFQ mission does not exist")
        self._validate_valid_until(
            valid_until=valid,
            now=submitted_at,
            mission_departure=mission.departure_window.start,
        )
        self._validate_aircraft(aircraft_id=aircraft_id, operator_id=rfq.operator_id)

        replacement = Quote.revise(
            previous,
            aircraft_id=aircraft_id,
            base_price=base_price,
            price_components=price_components,
            repositioning_cost=repositioning_cost,
            inclusions=inclusions,
            exclusions=exclusions,
            cancellation_terms=cancellation_terms,
            payment_terms=payment_terms,
            valid_until=valid,
            submitted_at=submitted_at,
            correlation_id=correlation_id,
        )
        expected_previous_version = previous.version
        previous.supersede(
            superseded_at=submitted_at,
            replacement_quote_id=replacement.id,
            correlation_id=correlation_id,
        )
        self._quotes.save(previous, expected_version=expected_previous_version)
        self._quotes.add(replacement)
        self._events.add_aggregate_events(previous)
        self._events.add_aggregate_events(replacement)
        return replacement

    def withdraw(
        self,
        *,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        quote = self._get_for_update(quote_id)
        if quote.status is not QuoteStatus.SUBMITTED or not quote.is_current:
            raise EntityConflictError("only the current submitted quote can be withdrawn")
        if when >= quote.valid_until:
            raise EntityConflictError("expired quote cannot be withdrawn; expire it instead")
        expected_version = quote.version
        quote.withdraw(withdrawn_at=when, correlation_id=correlation_id)
        self._quotes.save(quote, expected_version=expected_version)
        self._events.add_aggregate_events(quote)
        return quote

    def expire(
        self,
        *,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Quote:
        when = _utc(now, field_name="now")
        quote = self._get_for_update(quote_id)
        if quote.status is not QuoteStatus.SUBMITTED or not quote.is_current:
            raise EntityConflictError("only the current submitted quote can expire")
        if when < quote.valid_until:
            raise EntityConflictError("quote valid_until has not arrived")
        expected_version = quote.version
        quote.expire(expired_at=when, correlation_id=correlation_id)
        self._quotes.save(quote, expected_version=expected_version)
        self._events.add_aggregate_events(quote)
        return quote

    def get(self, quote_id: QuoteId) -> Quote:
        quote = self._quotes.get(quote_id)
        if quote is None:
            raise EntityNotFoundError("quote does not exist")
        return quote

    def list_for_rfq(self, rfq_id: RfqId) -> tuple[Quote, ...]:
        if self._rfqs.get(rfq_id) is None:
            raise EntityNotFoundError("RFQ does not exist")
        return self._quotes.list_for_rfq(rfq_id)

    def _get_for_update(self, quote_id: QuoteId) -> Quote:
        quote = self._quotes.get_for_update(quote_id)
        if quote is None:
            raise EntityNotFoundError("quote does not exist")
        return quote

    def _validate_aircraft(self, *, aircraft_id: AircraftId, operator_id: object) -> None:
        aircraft = self._aircraft.get(aircraft_id)
        if aircraft is None:
            raise EntityNotFoundError("aircraft does not exist")
        if aircraft.operator_id != operator_id:
            raise EntityConflictError("aircraft does not belong to the RFQ target operator")

    @staticmethod
    def _validate_valid_until(
        *,
        valid_until: datetime,
        now: datetime,
        mission_departure: datetime,
    ) -> None:
        if valid_until <= now:
            raise EntityConflictError("quote valid_until must be in the future")
        if valid_until >= mission_departure:
            raise EntityConflictError("quote valid_until must precede mission departure")
