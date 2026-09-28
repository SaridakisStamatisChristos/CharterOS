from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.main import create_app
from charteros.infrastructure.db.models.catalog import AircraftRow, OperatorRow
from charteros.infrastructure.db.models.fleet import (
    AircraftAvailabilityRecordRow,
    AircraftPositionObservationRow,
)
from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
from charteros.infrastructure.db.models.missions import MissionRow
from tests.integration.matching_support import (
    insert_profile,
    settings,
    setup_matching_state,
)


@pytest.mark.integration
def test_required_hard_constraints_are_enforced_on_persisted_state() -> None:
    settings_value = settings()
    with TestClient(create_app(settings_value)) as client:
        mission_id, aircraft_id, _, aircraft_type_id, _ = setup_matching_state(
            client,
            suffix="IB",
        )
        insert_profile(
            settings_value,
            aircraft_type_id,
            recorded_at=datetime.now(UTC),
        )

        baseline = client.get(f"/v1/missions/{mission_id}/matches")
        assert baseline.status_code == 200
        assert aircraft_id in {item["aircraft_id"] for item in baseline.json()["matches"]}

        engine = create_engine(settings_value.database_url)
        try:
            aircraft_uuid = UUID(aircraft_id)

            def assert_reason(reason: str) -> None:
                response = client.get(f"/v1/missions/{mission_id}/matches")
                assert response.status_code == 200
                assert aircraft_id not in {
                    item["aircraft_id"] for item in response.json()["matches"]
                }
                assert response.json()["rejection_summary"].get(reason, 0) >= 1

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                aircraft.status = "maintenance"
            assert_reason("aircraft_inactive")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                aircraft.status = "active"
                aircraft.seat_capacity = 99
            assert_reason("insufficient_capacity")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                aircraft.seat_capacity = 180
                aircraft.range_nm = 1
            assert_reason("insufficient_range")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                aircraft.range_nm = 3200
                operator = session.get(OperatorRow, aircraft.operator_id)
                assert operator is not None
                operator.verification_status = "pending"
            assert_reason("operator_unverified")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                operator = session.get(OperatorRow, aircraft.operator_id)
                assert operator is not None
                operator.verification_status = "verified"
                operator.insurance_status = "expired"
            assert_reason("operator_insurance_invalid")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                operator = session.get(OperatorRow, aircraft.operator_id)
                assert operator is not None
                operator.insurance_status = "valid"
                operator.commercial_status = "suspended"
            assert_reason("operator_commercial_inactive")

            with Session(engine) as session, session.begin():
                aircraft = session.get(AircraftRow, aircraft_uuid)
                assert aircraft is not None
                operator = session.get(OperatorRow, aircraft.operator_id)
                assert operator is not None
                operator.commercial_status = "active"
                availability = session.scalar(
                    select(AircraftAvailabilityRecordRow).where(
                        AircraftAvailabilityRecordRow.aircraft_id == aircraft_uuid,
                        AircraftAvailabilityRecordRow.superseded_at.is_(None),
                    )
                )
                assert availability is not None
                availability.status = "reserved"
            assert_reason("not_available")

            with Session(engine) as session, session.begin():
                availability = session.scalar(
                    select(AircraftAvailabilityRecordRow).where(
                        AircraftAvailabilityRecordRow.aircraft_id == aircraft_uuid,
                        AircraftAvailabilityRecordRow.superseded_at.is_(None),
                    )
                )
                assert availability is not None
                availability.status = "available"
                position = session.scalar(
                    select(AircraftPositionObservationRow).where(
                        AircraftPositionObservationRow.aircraft_id == aircraft_uuid
                    )
                )
                assert position is not None
                original_recorded_at = position.recorded_at
                position.recorded_at = datetime.now(UTC) + timedelta(days=1)
            assert_reason("no_position")

            with Session(engine) as session, session.begin():
                position = session.scalar(
                    select(AircraftPositionObservationRow).where(
                        AircraftPositionObservationRow.aircraft_id == aircraft_uuid
                    )
                )
                assert position is not None
                position.recorded_at = original_recorded_at
                original_airport_id = position.airport_id
                mission = session.get(MissionRow, UUID(mission_id))
                assert mission is not None
                position.airport_id = mission.destination_airport_id
                profile = session.scalar(
                    select(MatchingReferenceProfileRow).where(
                        MatchingReferenceProfileRow.aircraft_type_id == aircraft_type_id,
                        MatchingReferenceProfileRow.superseded_at.is_(None),
                    )
                )
                assert profile is not None
                profile.max_reposition_nm = 1
            assert_reason("reposition_too_far")

            with Session(engine) as session, session.begin():
                position = session.scalar(
                    select(AircraftPositionObservationRow).where(
                        AircraftPositionObservationRow.aircraft_id == aircraft_uuid
                    )
                )
                profile = session.scalar(
                    select(MatchingReferenceProfileRow).where(
                        MatchingReferenceProfileRow.aircraft_type_id == aircraft_type_id,
                        MatchingReferenceProfileRow.superseded_at.is_(None),
                    )
                )
                assert position is not None and profile is not None
                position.airport_id = original_airport_id
                profile.max_reposition_nm = 1_000
                profile.turnaround_buffer_minutes = 100_000
            assert_reason("reposition_too_late")
        finally:
            engine.dispose()
