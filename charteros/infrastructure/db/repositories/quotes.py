from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import AircraftId
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
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.infrastructure.db.models.quotes import QuotePriceComponentRow, QuoteRow


def _to_domain(row: QuoteRow) -> Quote:
    currency = Currency(row.currency)
    components = tuple(
        PriceComponent(
            category=PriceComponentCategory(component.category),
            label=component.label,
            amount=Money(component.amount_minor, currency),
            applicability=PriceComponentApplicability(component.applicability),
            condition=component.condition,
        )
        for component in row.components
    )
    repositioning = (
        Money(row.repositioning_amount_minor, currency)
        if row.repositioning_amount_minor is not None
        else None
    )
    return Quote(
        QuoteId(row.id),
        rfq_id=RfqId(row.rfq_id),
        aircraft_id=AircraftId(row.aircraft_id),
        base_price=Money(row.base_amount_minor, currency),
        price_components=components,
        repositioning_cost=repositioning,
        inclusions=tuple(row.inclusions),
        exclusions=tuple(row.exclusions),
        cancellation_terms=row.cancellation_terms,
        payment_terms=row.payment_terms,
        valid_until=row.valid_until,
        status=QuoteStatus(row.status),
        revision_number=row.revision_number,
        supersedes_quote_id=(
            QuoteId(row.supersedes_quote_id) if row.supersedes_quote_id is not None else None
        ),
        submitted_at=row.submitted_at,
        is_current=row.is_current,
        expired_at=row.expired_at,
        withdrawn_at=row.withdrawn_at,
        superseded_at=row.superseded_at,
        version=row.version,
    )


class SqlAlchemyQuoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, quote: Quote) -> None:
        row = QuoteRow(
            id=quote.id.value,
            version=quote.version,
            rfq_id=quote.rfq_id.value,
            aircraft_id=quote.aircraft_id.value,
            currency=str(quote.currency),
            base_amount_minor=quote.base_price.amount_minor,
            repositioning_amount_minor=(
                quote.repositioning_cost.amount_minor
                if quote.repositioning_cost is not None
                else None
            ),
            inclusions=list(quote.inclusions),
            exclusions=list(quote.exclusions),
            cancellation_terms=quote.cancellation_terms,
            payment_terms=quote.payment_terms,
            valid_until=quote.valid_until,
            status=quote.status.value,
            revision_number=quote.revision_number,
            supersedes_quote_id=(
                quote.supersedes_quote_id.value if quote.supersedes_quote_id is not None else None
            ),
            submitted_at=quote.submitted_at,
            is_current=quote.is_current,
            expired_at=quote.expired_at,
            withdrawn_at=quote.withdrawn_at,
            superseded_at=quote.superseded_at,
        )
        row.components = [
            QuotePriceComponentRow(
                quote_id=quote.id.value,
                line_number=index,
                category=component.category.value,
                label=component.label,
                amount_minor=component.amount.amount_minor,
                applicability=component.applicability.value,
                condition=component.condition,
            )
            for index, component in enumerate(quote.price_components)
        ]
        self._session.add(row)
        self._flush(
            "quote conflicts with persisted state; an authoritative RFQ revision may already exist"
        )

    def get(self, quote_id: QuoteId) -> Quote | None:
        row = self._session.get(QuoteRow, quote_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_update(self, quote_id: QuoteId) -> Quote | None:
        row = self._session.scalar(
            select(QuoteRow).where(QuoteRow.id == quote_id.value).with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def get_current_for_rfq(self, rfq_id: RfqId) -> Quote | None:
        row = self._session.scalar(
            select(QuoteRow).where(
                QuoteRow.rfq_id == rfq_id.value,
                QuoteRow.is_current.is_(True),
            )
        )
        return _to_domain(row) if row is not None else None

    def list_for_rfq(self, rfq_id: RfqId) -> tuple[Quote, ...]:
        rows = self._session.scalars(
            select(QuoteRow)
            .where(QuoteRow.rfq_id == rfq_id.value)
            .order_by(QuoteRow.revision_number)
        ).all()
        return tuple(_to_domain(row) for row in rows)

    def save(self, quote: Quote, *, expected_version: int) -> None:
        statement = (
            update(QuoteRow)
            .where(QuoteRow.id == quote.id.value, QuoteRow.version == expected_version)
            .values(
                version=quote.version,
                status=quote.status.value,
                is_current=quote.is_current,
                expired_at=quote.expired_at,
                withdrawn_at=quote.withdrawn_at,
                superseded_at=quote.superseded_at,
            )
            .returning(QuoteRow.id)
        )
        updated_id = self._session.scalar(statement)
        if updated_id is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for quote {quote.id}"
            )
        self._session.flush()

    def _flush(self, conflict_message: str) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(conflict_message) from exc
