from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import AircraftId
from charteros.domain.airports import AirportId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.normalization import normalize_quote
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow
from charteros.infrastructure.db.repositories.quotes import SqlAlchemyQuoteRepository
from charteros.repositioning import QuotedFutureLeg

_ACTIVE_MISSION_STATUSES = ("sourcing", "quoted")
_ACTIVE_SEALED_TENDER_STATUSES = ("draft", "open", "best_and_final")


class SqlAlchemyRepositionOpportunityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._quotes = SqlAlchemyQuoteRepository(session)

    def list_quoted_future_legs(
        self,
        *,
        aircraft_ids: tuple[AircraftId, ...],
        operator_ids: tuple[OperatorId, ...],
        window_start: datetime,
        window_end: datetime,
        evaluated_at: datetime,
        limit: int,
    ) -> tuple[QuotedFutureLeg, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not aircraft_ids or not operator_ids:
            return ()

        sealed_tender_exists = exists(
            select(TenderInvitationRow.id)
            .join(TenderRow, TenderRow.id == TenderInvitationRow.tender_id)
            .where(
                TenderInvitationRow.rfq_id == RfqRow.id,
                TenderRow.sealed_bid.is_(True),
                TenderRow.status.in_(_ACTIVE_SEALED_TENDER_STATUSES),
            )
        )

        rows = self._session.execute(
            select(
                MissionRow.id.label("mission_id"),
                MissionRow.origin_airport_id,
                MissionRow.destination_airport_id,
                MissionRow.departure_from,
                MissionRow.departure_to,
                MissionRow.passenger_count,
                RfqRow.id.label("rfq_id"),
                RfqRow.operator_id,
                QuoteRow.id.label("quote_id"),
                QuoteRow.aircraft_id,
            )
            .join(RfqRow, RfqRow.mission_id == MissionRow.id)
            .join(QuoteRow, QuoteRow.rfq_id == RfqRow.id)
            .where(
                MissionRow.status.in_(_ACTIVE_MISSION_STATUSES),
                MissionRow.created_at <= evaluated_at,
                MissionRow.departure_to > evaluated_at,
                MissionRow.departure_to > window_start,
                MissionRow.departure_from < window_end,
                RfqRow.status == "quoted",
                RfqRow.operator_id.in_([item.value for item in operator_ids]),
                QuoteRow.aircraft_id.in_([item.value for item in aircraft_ids]),
                QuoteRow.status == "submitted",
                QuoteRow.is_current.is_(True),
                QuoteRow.submitted_at <= evaluated_at,
                QuoteRow.valid_until > evaluated_at,
                ~sealed_tender_exists,
            )
            .order_by(MissionRow.departure_from, MissionRow.id, QuoteRow.id)
            .limit(limit + 1)
        ).all()
        if len(rows) > limit:
            raise EntityConflictError(
                f"reposition opportunity set exceeds bounded PR18 limit of {limit}"
            )

        result: list[QuotedFutureLeg] = []
        for row in rows:
            quote = self._quotes.get(QuoteId(row.quote_id))
            if quote is None:
                raise EntityConflictError("reposition opportunity quote disappeared during read")
            normalized = normalize_quote(quote)
            result.append(
                QuotedFutureLeg(
                    mission_id=MissionId(row.mission_id),
                    rfq_id=RfqId(row.rfq_id),
                    quote_id=quote.id,
                    aircraft_id=AircraftId(row.aircraft_id),
                    operator_id=OperatorId(row.operator_id),
                    origin_airport_id=AirportId(row.origin_airport_id),
                    destination_airport_id=AirportId(row.destination_airport_id),
                    departure_window=TimeRange(row.departure_from, row.departure_to),
                    passenger_count=int(row.passenger_count),
                    revenue=normalized.expected_total,
                    worst_case_revenue=normalized.worst_case_total,
                    totals_complete=normalized.totals_complete,
                    pricing_confidence=normalized.confidence,
                )
            )
        return tuple(result)
