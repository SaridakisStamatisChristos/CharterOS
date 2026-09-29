from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.operator_portal import (
    PortalAircraft,
    PortalAircraftPage,
    PortalBooking,
    PortalBookingPage,
    PortalQuoteContext,
    PortalRfq,
    PortalRfqPage,
)
from charteros.domain.aircraft import AircraftStatus
from charteros.domain.bookings import BookingState
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import RfqStatus
from charteros.infrastructure.db.models.bookings import BookingRow
from charteros.infrastructure.db.models.catalog import (
    AircraftRow,
    AircraftTypeRow,
    AirportRow,
    OperatorRow,
)
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.quotes import QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow


class SqlAlchemyOperatorPortalRepository:
    """Bounded operator-scoped read models over canonical PostgreSQL state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def operator_exists(self, operator_id: OperatorId) -> bool:
        return self._session.get(OperatorRow, operator_id.value) is not None

    def list_aircraft(
        self,
        operator_id: OperatorId,
        *,
        limit: int,
        cursor: UUID | None,
    ) -> PortalAircraftPage:
        statement = self._aircraft_statement().where(AircraftRow.operator_id == operator_id.value)
        if cursor is not None:
            cursor_row = self._session.scalar(
                select(AircraftRow).where(
                    AircraftRow.id == cursor,
                    AircraftRow.operator_id == operator_id.value,
                )
            )
            if cursor_row is None:
                raise EntityNotFoundError("fleet cursor is not available in the operator context")
            statement = statement.where(
                or_(
                    AircraftRow.registration > cursor_row.registration,
                    and_(
                        AircraftRow.registration == cursor_row.registration,
                        AircraftRow.id > cursor_row.id,
                    ),
                )
            )
        rows = self._session.execute(
            statement.order_by(AircraftRow.registration, AircraftRow.id).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        items = tuple(self._aircraft_view(row) for row in page_rows)
        next_cursor = items[-1].id if len(rows) > limit and items else None
        return PortalAircraftPage(items=items, next_cursor=next_cursor)

    def get_aircraft(
        self,
        operator_id: OperatorId,
        aircraft_id: UUID,
    ) -> PortalAircraft | None:
        row = self._session.execute(
            self._aircraft_statement().where(
                AircraftRow.id == aircraft_id,
                AircraftRow.operator_id == operator_id.value,
            )
        ).one_or_none()
        return self._aircraft_view(row) if row is not None else None

    def list_rfqs(
        self,
        operator_id: OperatorId,
        *,
        statuses: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalRfqPage:
        statement = self._rfq_statement().where(RfqRow.operator_id == operator_id.value)
        if statuses:
            statement = statement.where(RfqRow.status.in_(statuses))
        if cursor is not None:
            cursor_row = self._session.scalar(
                select(RfqRow).where(
                    RfqRow.id == cursor,
                    RfqRow.operator_id == operator_id.value,
                )
            )
            if cursor_row is None:
                raise EntityNotFoundError("RFQ cursor is not available in the operator context")
            statement = statement.where(
                or_(
                    RfqRow.created_at < cursor_row.created_at,
                    and_(
                        RfqRow.created_at == cursor_row.created_at,
                        RfqRow.id < cursor_row.id,
                    ),
                )
            )
        rows = self._session.execute(
            statement.order_by(RfqRow.created_at.desc(), RfqRow.id.desc()).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        items = tuple(self._rfq_view(row) for row in page_rows)
        next_cursor = items[-1].id if len(rows) > limit and items else None
        return PortalRfqPage(items=items, next_cursor=next_cursor)

    def get_rfq(
        self,
        operator_id: OperatorId,
        rfq_id: UUID,
    ) -> PortalRfq | None:
        row = self._session.execute(
            self._rfq_statement().where(
                RfqRow.id == rfq_id,
                RfqRow.operator_id == operator_id.value,
            )
        ).one_or_none()
        return self._rfq_view(row) if row is not None else None

    def get_quote_context(
        self,
        operator_id: OperatorId,
        quote_id: UUID,
    ) -> PortalQuoteContext | None:
        rfq_id = self._session.scalar(
            select(QuoteRow.rfq_id)
            .join(RfqRow, RfqRow.id == QuoteRow.rfq_id)
            .where(
                QuoteRow.id == quote_id,
                RfqRow.operator_id == operator_id.value,
            )
        )
        if rfq_id is None:
            return None
        rfq = self.get_rfq(operator_id, rfq_id)
        if rfq is None:
            raise EntityConflictError("operator quote references an unreadable RFQ")
        return PortalQuoteContext(quote_id=quote_id, rfq=rfq)

    def list_bookings(
        self,
        operator_id: OperatorId,
        *,
        states: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage:
        statement = self._booking_statement().where(BookingRow.operator_id == operator_id.value)
        if states:
            statement = statement.where(BookingRow.state.in_(states))
        if cursor is not None:
            cursor_row = self._session.scalar(
                select(BookingRow).where(
                    BookingRow.id == cursor,
                    BookingRow.operator_id == operator_id.value,
                )
            )
            if cursor_row is None:
                raise EntityNotFoundError("booking cursor is not available in the operator context")
            statement = statement.where(
                or_(
                    BookingRow.state_changed_at < cursor_row.state_changed_at,
                    and_(
                        BookingRow.state_changed_at == cursor_row.state_changed_at,
                        BookingRow.id < cursor_row.id,
                    ),
                )
            )
        rows = self._session.execute(
            statement.order_by(BookingRow.state_changed_at.desc(), BookingRow.id.desc()).limit(
                limit + 1
            )
        ).all()
        page_rows = rows[:limit]
        items = tuple(self._booking_view(row) for row in page_rows)
        next_cursor = items[-1].id if len(rows) > limit and items else None
        return PortalBookingPage(items=items, next_cursor=next_cursor)

    def get_booking(
        self,
        operator_id: OperatorId,
        booking_id: UUID,
    ) -> PortalBooking | None:
        row = self._session.execute(
            self._booking_statement().where(
                BookingRow.id == booking_id,
                BookingRow.operator_id == operator_id.value,
            )
        ).one_or_none()
        return self._booking_view(row) if row is not None else None

    def calendar(
        self,
        operator_id: OperatorId,
        *,
        window_start: datetime,
        window_end: datetime,
        states: tuple[str, ...],
        limit: int,
        cursor: UUID | None,
    ) -> PortalBookingPage:
        statement = self._booking_statement().where(
            BookingRow.operator_id == operator_id.value,
            MissionRow.departure_from < window_end,
            MissionRow.departure_to > window_start,
        )
        if states:
            statement = statement.where(BookingRow.state.in_(states))
        if cursor is not None:
            cursor_row = self._session.execute(
                select(BookingRow, MissionRow)
                .join(MissionRow, MissionRow.id == BookingRow.mission_id)
                .where(
                    BookingRow.id == cursor,
                    BookingRow.operator_id == operator_id.value,
                )
            ).one_or_none()
            if cursor_row is None:
                raise EntityNotFoundError(
                    "calendar cursor is not available in the operator context"
                )
            cursor_booking, cursor_mission = cursor_row
            statement = statement.where(
                or_(
                    MissionRow.departure_from > cursor_mission.departure_from,
                    and_(
                        MissionRow.departure_from == cursor_mission.departure_from,
                        BookingRow.id > cursor_booking.id,
                    ),
                )
            )
        rows = self._session.execute(
            statement.order_by(MissionRow.departure_from, BookingRow.id).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        items = tuple(self._booking_view(row) for row in page_rows)
        next_cursor = items[-1].id if len(rows) > limit and items else None
        return PortalBookingPage(items=items, next_cursor=next_cursor)

    @staticmethod
    def _aircraft_statement() -> Select[AircraftRow, AircraftTypeRow, AirportRow]:
        return (
            select(AircraftRow, AircraftTypeRow, AirportRow)
            .join(AircraftTypeRow, AircraftTypeRow.id == AircraftRow.aircraft_type_id)
            .join(AirportRow, AirportRow.id == AircraftRow.home_base_id)
        )

    @staticmethod
    def _aircraft_view(row: Row[AircraftRow, AircraftTypeRow, AirportRow]) -> PortalAircraft:
        aircraft = row[0]
        aircraft_type = row[1]
        home_base = row[2]
        if (
            not isinstance(aircraft, AircraftRow)
            or not isinstance(aircraft_type, AircraftTypeRow)
            or not isinstance(home_base, AirportRow)
        ):
            raise EntityConflictError("operator fleet query returned an invalid canonical shape")
        return PortalAircraft(
            id=aircraft.id,
            version=aircraft.version,
            operator_id=aircraft.operator_id,
            registration=aircraft.registration,
            aircraft_type_id=aircraft.aircraft_type_id,
            manufacturer=aircraft_type.manufacturer,
            model=aircraft_type.model,
            category=aircraft_type.category,
            seat_capacity=aircraft.seat_capacity,
            cargo_capacity=aircraft.cargo_capacity,
            range_nm=aircraft.range_nm,
            home_base_id=aircraft.home_base_id,
            home_base_icao=home_base.icao,
            status=AircraftStatus(aircraft.status),
        )

    @staticmethod
    def _rfq_statement() -> Select[
        RfqRow,
        MissionRow,
        str,
        str,
        QuoteRow,
        TenderInvitationRow,
        TenderRow,
    ]:
        origin = aliased(AirportRow)
        destination = aliased(AirportRow)
        return (
            select(
                RfqRow,
                MissionRow,
                origin.icao,
                destination.icao,
                QuoteRow,
                TenderInvitationRow,
                TenderRow,
            )
            .join(MissionRow, MissionRow.id == RfqRow.mission_id)
            .join(origin, origin.id == MissionRow.origin_airport_id)
            .join(destination, destination.id == MissionRow.destination_airport_id)
            .outerjoin(
                QuoteRow,
                and_(
                    QuoteRow.rfq_id == RfqRow.id,
                    QuoteRow.is_current.is_(True),
                ),
            )
            .outerjoin(TenderInvitationRow, TenderInvitationRow.rfq_id == RfqRow.id)
            .outerjoin(TenderRow, TenderRow.id == TenderInvitationRow.tender_id)
        )

    @staticmethod
    def _rfq_view(
        row: Row[RfqRow, MissionRow, str, str, QuoteRow, TenderInvitationRow, TenderRow],
    ) -> PortalRfq:
        rfq = row[0]
        mission = row[1]
        current_quote = row[4]
        invitation = row[5]
        tender = row[6]
        if not isinstance(rfq, RfqRow) or not isinstance(mission, MissionRow):
            raise EntityConflictError("operator RFQ query returned an invalid canonical shape")
        if current_quote is not None and not isinstance(current_quote, QuoteRow):
            raise EntityConflictError("operator RFQ current quote has invalid shape")
        if invitation is not None and not isinstance(invitation, TenderInvitationRow):
            raise EntityConflictError("operator RFQ tender invitation has invalid shape")
        if tender is not None and not isinstance(tender, TenderRow):
            raise EntityConflictError("operator RFQ tender has invalid shape")
        if invitation is not None and invitation.operator_id != rfq.operator_id:
            raise EntityConflictError("tender invitation conflicts with RFQ operator ownership")
        if invitation is not None and tender is None:
            raise EntityConflictError("tender invitation references missing tender evidence")
        return PortalRfq(
            id=rfq.id,
            version=rfq.version,
            operator_id=rfq.operator_id,
            status=RfqStatus(rfq.status),
            created_at=rfq.created_at,
            sent_at=rfq.sent_at,
            response_deadline=rfq.response_deadline,
            acknowledged_at=rfq.acknowledged_at,
            declined_at=rfq.declined_at,
            expired_at=rfq.expired_at,
            decline_reason=rfq.decline_reason,
            mission_id=mission.id,
            mission_status=mission.status,
            origin_airport_id=mission.origin_airport_id,
            origin_icao=str(row[2]),
            destination_airport_id=mission.destination_airport_id,
            destination_icao=str(row[3]),
            departure_from=mission.departure_from,
            departure_to=mission.departure_to,
            passenger_count=mission.passenger_count,
            special_requirements=tuple(mission.special_requirements),
            current_quote_id=current_quote.id if isinstance(current_quote, QuoteRow) else None,
            current_quote_status=(
                current_quote.status if isinstance(current_quote, QuoteRow) else None
            ),
            current_quote_revision=(
                current_quote.revision_number if isinstance(current_quote, QuoteRow) else None
            ),
            tender_id=tender.id if isinstance(tender, TenderRow) else None,
            tender_status=tender.status if isinstance(tender, TenderRow) else None,
            tender_sealed_bid=tender.sealed_bid if isinstance(tender, TenderRow) else None,
            tender_invitation_id=(
                invitation.id if isinstance(invitation, TenderInvitationRow) else None
            ),
            tender_invitation_status=(
                invitation.status if isinstance(invitation, TenderInvitationRow) else None
            ),
        )

    @staticmethod
    def _booking_statement() -> Select[
        BookingRow,
        MissionRow,
        QuoteRow,
        RfqRow,
        AircraftRow,
        str,
        str,
    ]:
        origin = aliased(AirportRow)
        destination = aliased(AirportRow)
        return (
            select(
                BookingRow,
                MissionRow,
                QuoteRow,
                RfqRow,
                AircraftRow,
                origin.icao,
                destination.icao,
            )
            .join(MissionRow, MissionRow.id == BookingRow.mission_id)
            .join(QuoteRow, QuoteRow.id == BookingRow.accepted_quote_id)
            .join(RfqRow, RfqRow.id == QuoteRow.rfq_id)
            .join(AircraftRow, AircraftRow.id == BookingRow.aircraft_id)
            .join(origin, origin.id == MissionRow.origin_airport_id)
            .join(destination, destination.id == MissionRow.destination_airport_id)
        )

    @staticmethod
    def _booking_view(
        row: Row[BookingRow, MissionRow, QuoteRow, RfqRow, AircraftRow, str, str],
    ) -> PortalBooking:
        booking = row[0]
        mission = row[1]
        quote = row[2]
        rfq = row[3]
        aircraft = row[4]
        if (
            not isinstance(booking, BookingRow)
            or not isinstance(mission, MissionRow)
            or not isinstance(quote, QuoteRow)
            or not isinstance(rfq, RfqRow)
            or not isinstance(aircraft, AircraftRow)
        ):
            raise EntityConflictError("operator booking query returned an invalid canonical shape")
        if (
            booking.mission_id != mission.id
            or booking.accepted_quote_id != quote.id
            or quote.rfq_id != rfq.id
            or rfq.mission_id != booking.mission_id
            or rfq.operator_id != booking.operator_id
            or quote.aircraft_id != booking.aircraft_id
            or aircraft.id != booking.aircraft_id
            or aircraft.operator_id != booking.operator_id
        ):
            raise EntityConflictError(
                "booking lineage conflicts with canonical operator/mission/quote/aircraft ownership"
            )
        return PortalBooking(
            id=booking.id,
            version=booking.version,
            mission_id=booking.mission_id,
            accepted_quote_id=booking.accepted_quote_id,
            operator_id=booking.operator_id,
            aircraft_id=booking.aircraft_id,
            state=BookingState(booking.state),
            created_at=booking.created_at,
            state_changed_at=booking.state_changed_at,
            origin_airport_id=mission.origin_airport_id,
            origin_icao=str(row[5]),
            destination_airport_id=mission.destination_airport_id,
            destination_icao=str(row[6]),
            departure_from=mission.departure_from,
            departure_to=mission.departure_to,
            quote_currency=quote.currency,
        )
