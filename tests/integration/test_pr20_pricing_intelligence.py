import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text

from charteros.application.pricing_intelligence import PricingIntelligenceService
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.models.bookings import BookingRow
from charteros.infrastructure.db.models.catalog import (
    AircraftRow,
    AircraftTypeRow,
    AirportRow,
    OperatorRow,
    OrganizationRow,
)
from charteros.infrastructure.db.models.fleet import AircraftPositionObservationRow
from charteros.infrastructure.db.models.missions import MissionRow
from charteros.infrastructure.db.models.quotes import QuotePriceComponentRow, QuoteRow
from charteros.infrastructure.db.models.rfqs import RfqRow
from charteros.infrastructure.db.models.tenders import TenderInvitationRow, TenderRow
from charteros.infrastructure.db.repositories import SqlAlchemyPricingIntelligenceRepository
from charteros.pricing_intelligence import PositionState
from charteros.shared.config import Settings

BASE = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _airport(airport_id: UUID, *, icao: str, lat: str, lon: str) -> AirportRow:
    return AirportRow(
        id=airport_id,
        version=1,
        icao=icao,
        iata=None,
        latitude=Decimal(lat),
        longitude=Decimal(lon),
        timezone="UTC",
        runway_metadata={},
        curfew_metadata={},
        operational_flags=[],
    )


@pytest.mark.integration
def test_pr20_dataset_uses_quote_time_position_and_hides_active_sealed_tender() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    origin_id, destination_id, other_id = (uuid4() for _ in range(3))
    buyer_id, operator_org_id = uuid4(), uuid4()
    operator_id, aircraft_type_id, aircraft_id = uuid4(), uuid4(), uuid4()
    direct_mission_id, sealed_mission_id = uuid4(), uuid4()
    direct_rfq_id, sealed_rfq_id = uuid4(), uuid4()
    direct_quote_id, sealed_quote_id = uuid4(), uuid4()
    booking_id = uuid4()
    tender_id, invitation_id = uuid4(), uuid4()
    known_position_id, hindsight_position_id = uuid4(), uuid4()

    submitted_at = BASE
    departure = BASE + timedelta(days=10)
    deadline = BASE + timedelta(days=2)

    try:
        with factory.begin() as session:
            session.add_all(
                [
                    _airport(origin_id, icao="P20A", lat="37.93640", lon="23.94450"),
                    _airport(destination_id, icao="P20B", lat="40.51970", lon="22.97090"),
                    _airport(other_id, icao="P20C", lat="35.33970", lon="25.18030"),
                ]
            )
            session.add_all(
                [
                    OrganizationRow(
                        id=buyer_id,
                        version=1,
                        type="buyer",
                        legal_name=f"PR20 Buyer {buyer_id.hex[:8]}",
                        legal_name_key=f"pr20 buyer {buyer_id.hex[:8]}",
                        trading_name=None,
                        country="GR",
                        status="active",
                    ),
                    OrganizationRow(
                        id=operator_org_id,
                        version=1,
                        type="operator",
                        legal_name=f"PR20 Operator {operator_org_id.hex[:8]}",
                        legal_name_key=f"pr20 operator {operator_org_id.hex[:8]}",
                        trading_name=None,
                        country="GR",
                        status="active",
                    ),
                ]
            )
            session.flush()
            session.add(
                OperatorRow(
                    id=operator_id,
                    version=1,
                    organization_id=operator_org_id,
                    aoc_reference=f"GR-P20-{operator_id.hex[:8]}",
                    operating_regions=["EU"],
                    verification_status="verified",
                    insurance_status="valid",
                    safety_documents=[],
                    commercial_status="active",
                )
            )
            session.add(
                AircraftTypeRow(
                    id=aircraft_type_id,
                    manufacturer="PR20 Airframes",
                    manufacturer_key=f"pr20 airframes {aircraft_type_id.hex[:8]}",
                    model=f"HistoryJet-{aircraft_type_id.hex[:8]}",
                    model_key=f"historyjet-{aircraft_type_id.hex[:8]}",
                    category="midsize",
                    seats_min=1,
                    seats_max=12,
                    range_nm=3000,
                    runway_requirements={},
                    baggage_cargo_profile={},
                )
            )
            session.flush()
            session.add(
                AircraftRow(
                    id=aircraft_id,
                    version=1,
                    operator_id=operator_id,
                    registration=f"P20-{aircraft_id.hex[:8].upper()}",
                    aircraft_type_id=aircraft_type_id,
                    seat_capacity=10,
                    cargo_capacity=Decimal("500"),
                    range_nm=2500,
                    home_base_id=origin_id,
                    status="active",
                )
            )
            session.add_all(
                [
                    MissionRow(
                        id=direct_mission_id,
                        version=3,
                        buyer_id=buyer_id,
                        origin_airport_id=origin_id,
                        destination_airport_id=destination_id,
                        departure_from=departure,
                        departure_to=departure + timedelta(hours=2),
                        passenger_count=6,
                        max_budget_amount_minor=None,
                        max_budget_currency=None,
                        special_requirements=[],
                        status="selected",
                        created_at=BASE - timedelta(days=1),
                    ),
                    MissionRow(
                        id=sealed_mission_id,
                        version=2,
                        buyer_id=buyer_id,
                        origin_airport_id=origin_id,
                        destination_airport_id=other_id,
                        departure_from=departure + timedelta(days=1),
                        departure_to=departure + timedelta(days=1, hours=2),
                        passenger_count=5,
                        max_budget_amount_minor=None,
                        max_budget_currency=None,
                        special_requirements=[],
                        status="sourcing",
                        created_at=BASE - timedelta(days=1),
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    RfqRow(
                        id=direct_rfq_id,
                        version=4,
                        mission_id=direct_mission_id,
                        operator_id=operator_id,
                        status="quoted",
                        created_at=BASE - timedelta(hours=4),
                        sent_at=BASE - timedelta(hours=3),
                        response_deadline=deadline,
                        acknowledged_at=BASE - timedelta(hours=2),
                        declined_at=None,
                        expired_at=None,
                        decline_reason=None,
                    ),
                    RfqRow(
                        id=sealed_rfq_id,
                        version=4,
                        mission_id=sealed_mission_id,
                        operator_id=operator_id,
                        status="quoted",
                        created_at=BASE - timedelta(hours=4),
                        sent_at=BASE - timedelta(hours=3),
                        response_deadline=deadline,
                        acknowledged_at=BASE - timedelta(hours=2),
                        declined_at=None,
                        expired_at=None,
                        decline_reason=None,
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    QuoteRow(
                        id=direct_quote_id,
                        version=2,
                        rfq_id=direct_rfq_id,
                        aircraft_id=aircraft_id,
                        currency="EUR",
                        base_amount_minor=1_500_000,
                        repositioning_amount_minor=100_000,
                        inclusions=[],
                        exclusions=[],
                        cancellation_terms=None,
                        payment_terms=None,
                        valid_until=BASE + timedelta(days=1),
                        status="accepted",
                        revision_number=1,
                        supersedes_quote_id=None,
                        submitted_at=submitted_at,
                        is_current=False,
                        accepted_at=BASE + timedelta(hours=1),
                        rejected_at=None,
                        expired_at=None,
                        withdrawn_at=None,
                        superseded_at=None,
                    ),
                    QuoteRow(
                        id=sealed_quote_id,
                        version=1,
                        rfq_id=sealed_rfq_id,
                        aircraft_id=aircraft_id,
                        currency="EUR",
                        base_amount_minor=1_400_000,
                        repositioning_amount_minor=50_000,
                        inclusions=[],
                        exclusions=[],
                        cancellation_terms=None,
                        payment_terms=None,
                        valid_until=BASE + timedelta(days=1),
                        status="submitted",
                        revision_number=1,
                        supersedes_quote_id=None,
                        submitted_at=submitted_at + timedelta(minutes=5),
                        is_current=True,
                        accepted_at=None,
                        rejected_at=None,
                        expired_at=None,
                        withdrawn_at=None,
                        superseded_at=None,
                    ),
                ]
            )
            session.add(
                QuotePriceComponentRow(
                    quote_id=direct_quote_id,
                    line_number=0,
                    category="handling",
                    label="Handling",
                    amount_minor=75_000,
                    applicability="known",
                    condition=None,
                )
            )
            session.flush()
            session.add(
                BookingRow(
                    id=booking_id,
                    version=4,
                    mission_id=direct_mission_id,
                    accepted_quote_id=direct_quote_id,
                    operator_id=operator_id,
                    aircraft_id=aircraft_id,
                    state="confirmed",
                    created_at=BASE + timedelta(hours=1),
                    state_changed_at=BASE + timedelta(hours=4),
                )
            )
            session.add_all(
                [
                    AircraftPositionObservationRow(
                        id=known_position_id,
                        aircraft_id=aircraft_id,
                        airport_id=origin_id,
                        latitude=None,
                        longitude=None,
                        event_time=BASE - timedelta(hours=1),
                        recorded_at=BASE - timedelta(minutes=45),
                        source="pr20-known",
                        provenance={"kind": "known-at-quote"},
                    ),
                    AircraftPositionObservationRow(
                        id=hindsight_position_id,
                        aircraft_id=aircraft_id,
                        airport_id=other_id,
                        latitude=None,
                        longitude=None,
                        event_time=BASE - timedelta(minutes=30),
                        recorded_at=BASE + timedelta(minutes=30),
                        source="pr20-late",
                        provenance={"kind": "late-arriving"},
                    ),
                ]
            )
            session.add(
                TenderRow(
                    id=tender_id,
                    version=2,
                    mission_id=sealed_mission_id,
                    status="open",
                    sealed_bid=True,
                    opens_at=BASE - timedelta(hours=3),
                    deadline_at=deadline,
                    created_at=BASE - timedelta(hours=4),
                    opened_at=BASE - timedelta(hours=3),
                    best_and_final_requested_at=None,
                    closed_at=None,
                    awarded_quote_id=None,
                    booking_id=None,
                    awarded_at=None,
                )
            )
            session.flush()
            session.add(
                TenderInvitationRow(
                    id=invitation_id,
                    tender_id=tender_id,
                    operator_id=operator_id,
                    rfq_id=sealed_rfq_id,
                    status="accepted",
                    invited_at=BASE - timedelta(hours=3),
                    responded_at=BASE - timedelta(hours=2),
                    last_quote_id=sealed_quote_id,
                    best_and_final_quote_id=None,
                )
            )

        with factory() as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            dataset = PricingIntelligenceService(
                SqlAlchemyPricingIntelligenceRepository(session)
            ).build_dataset(
                source_window=TimeRange(
                    BASE - timedelta(hours=1),
                    BASE + timedelta(hours=1),
                ),
                limit=100,
            )

        assert dataset.row_count == 1
        assert dataset.truncated is False
        assert dataset.global_price_comparison_available is True
        row = dataset.rows[0]
        assert row.quote_id == direct_quote_id
        assert row.features.route_key == "P20A-P20B"
        assert row.features.aircraft_category == "midsize"
        assert row.features.position_state is PositionState.AT_ORIGIN
        assert row.features.position_age_minutes == 60
        assert row.features.normalized_expected_total_minor == 1_675_000
        assert row.features.normalized_worst_case_total_minor == 1_675_000
        assert row.outcomes.accepted is True
        assert row.outcomes.booked is True
        assert row.outcomes.booking_state is not None
        assert row.outcomes.booking_state.value == "confirmed"
        assert sealed_quote_id not in {item.quote_id for item in dataset.rows}
    finally:
        with factory.begin() as session:
            session.execute(
                delete(TenderInvitationRow).where(TenderInvitationRow.id == invitation_id)
            )
            session.execute(delete(TenderRow).where(TenderRow.id == tender_id))
            session.execute(delete(BookingRow).where(BookingRow.id == booking_id))
            session.execute(
                delete(QuotePriceComponentRow).where(
                    QuotePriceComponentRow.quote_id == direct_quote_id
                )
            )
            session.execute(
                delete(QuoteRow).where(QuoteRow.id.in_((direct_quote_id, sealed_quote_id)))
            )
            session.execute(
                delete(AircraftPositionObservationRow).where(
                    AircraftPositionObservationRow.id.in_(
                        (known_position_id, hindsight_position_id)
                    )
                )
            )
            session.execute(
                delete(RfqRow).where(RfqRow.id.in_((direct_rfq_id, sealed_rfq_id)))
            )
            session.execute(
                delete(MissionRow).where(
                    MissionRow.id.in_((direct_mission_id, sealed_mission_id))
                )
            )
            session.execute(delete(AircraftRow).where(AircraftRow.id == aircraft_id))
            session.execute(delete(AircraftTypeRow).where(AircraftTypeRow.id == aircraft_type_id))
            session.execute(delete(OperatorRow).where(OperatorRow.id == operator_id))
            session.execute(
                delete(OrganizationRow).where(
                    OrganizationRow.id.in_((buyer_id, operator_org_id))
                )
            )
            session.execute(
                delete(AirportRow).where(
                    AirportRow.id.in_((origin_id, destination_id, other_id))
                )
            )
        engine.dispose()
