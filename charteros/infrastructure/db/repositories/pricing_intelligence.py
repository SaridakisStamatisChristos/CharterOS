from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from charteros.domain.bookings import BookingState
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.bookings import BookingRow
from charteros.infrastructure.db.models.catalog import (
    AircraftRow,
    AircraftTypeRow,
    AirportRow,
)
from charteros.infrastructure.db.models.fleet import AircraftPositionObservationRow
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow
from charteros.infrastructure.db.repositories.quotes import quote_from_row
from charteros.pricing_intelligence import HistoricalPricingEvidence


class SqlAlchemyPricingIntelligenceRepository:
    """Read-only canonical historical evidence for PR20 feature derivation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_historical_pricing(
        self,
        *,
        source_window: TimeRange,
        limit: int,
    ) -> tuple[tuple[HistoricalPricingEvidence, ...], bool]:
        origin = aliased(AirportRow)
        destination = aliased(AirportRow)
        position = aliased(AircraftPositionObservationRow)

        latest_position_id = (
            select(AircraftPositionObservationRow.id)
            .where(
                AircraftPositionObservationRow.aircraft_id == QuoteRow.aircraft_id,
                AircraftPositionObservationRow.event_time <= QuoteRow.submitted_at,
                AircraftPositionObservationRow.recorded_at <= QuoteRow.submitted_at,
            )
            .order_by(
                AircraftPositionObservationRow.event_time.desc(),
                AircraftPositionObservationRow.recorded_at.desc(),
                AircraftPositionObservationRow.id.desc(),
            )
            .limit(1)
            .correlate(QuoteRow)
            .scalar_subquery()
        )

        statement = (
            select(
                QuoteRow,
                RfqRow,
                MissionRow,
                AircraftRow,
                AircraftTypeRow,
                origin,
                destination,
                BookingRow,
                position,
                TenderRow,
            )
            .options(selectinload(QuoteRow.components))
            .join(RfqRow, RfqRow.id == QuoteRow.rfq_id)
            .join(MissionRow, MissionRow.id == RfqRow.mission_id)
            .join(AircraftRow, AircraftRow.id == QuoteRow.aircraft_id)
            .join(AircraftTypeRow, AircraftTypeRow.id == AircraftRow.aircraft_type_id)
            .join(origin, origin.id == MissionRow.origin_airport_id)
            .join(destination, destination.id == MissionRow.destination_airport_id)
            .outerjoin(BookingRow, BookingRow.accepted_quote_id == QuoteRow.id)
            .outerjoin(TenderInvitationRow, TenderInvitationRow.rfq_id == RfqRow.id)
            .outerjoin(TenderRow, TenderRow.id == TenderInvitationRow.tender_id)
            .outerjoin(position, position.id == latest_position_id)
            .where(
                QuoteRow.submitted_at >= source_window.start,
                QuoteRow.submitted_at < source_window.end,
                or_(
                    TenderRow.id.is_(None),
                    TenderRow.sealed_bid.is_(False),
                    TenderRow.status.in_(("closed", "awarded")),
                ),
            )
            .order_by(QuoteRow.submitted_at, QuoteRow.id)
            .limit(limit + 1)
        )
        records = self._session.execute(statement).all()
        truncated = len(records) > limit

        evidence: list[HistoricalPricingEvidence] = []
        for (
            quote_row,
            rfq_row,
            mission_row,
            aircraft_row,
            aircraft_type_row,
            origin_row,
            destination_row,
            booking_row,
            position_row,
            tender_row,
        ) in records[:limit]:
            evidence.append(
                HistoricalPricingEvidence(
                    quote=quote_from_row(quote_row),
                    mission_id=mission_row.id,
                    operator_id=rfq_row.operator_id,
                    aircraft_id=aircraft_row.id,
                    aircraft_category=aircraft_type_row.category,
                    origin_airport_id=mission_row.origin_airport_id,
                    origin_icao=origin_row.icao,
                    destination_icao=destination_row.icao,
                    departure_window=TimeRange(
                        mission_row.departure_from,
                        mission_row.departure_to,
                    ),
                    booking_state=(
                        BookingState(booking_row.state) if booking_row is not None else None
                    ),
                    booking_created_at=(
                        booking_row.created_at if booking_row is not None else None
                    ),
                    position_airport_id=(
                        position_row.airport_id if position_row is not None else None
                    ),
                    position_event_time=(
                        position_row.event_time if position_row is not None else None
                    ),
                    position_recorded_at=(
                        position_row.recorded_at if position_row is not None else None
                    ),
                    position_has_coordinates=(
                        position_row is not None
                        and position_row.airport_id is None
                        and position_row.latitude is not None
                        and position_row.longitude is not None
                    ),
                    tender_id=tender_row.id if tender_row is not None else None,
                )
            )
        return tuple(evidence), truncated
