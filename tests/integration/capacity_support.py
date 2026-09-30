from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow


def seed_capacity_reference_profile(
    aircraft: dict[str, object],
    *,
    source: str,
    cruise_speed_kts: int = 450,
    turnaround_buffer_minutes: int = 45,
) -> None:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    aircraft_type_id = UUID(str(aircraft["aircraft_type_id"]))
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            existing = session.scalar(
                select(MatchingReferenceProfileRow).where(
                    MatchingReferenceProfileRow.aircraft_type_id == aircraft_type_id,
                    MatchingReferenceProfileRow.superseded_at.is_(None),
                )
            )
            if existing is not None:
                return
            session.add(
                MatchingReferenceProfileRow(
                    id=uuid4(),
                    aircraft_type_id=aircraft_type_id,
                    cruise_speed_kts=cruise_speed_kts,
                    operating_cost_per_hour_minor=600_000,
                    operating_cost_currency="EUR",
                    max_reposition_nm=1_000,
                    turnaround_buffer_minutes=turnaround_buffer_minutes,
                    source=source,
                    provenance={"fixture": "capacity-reference"},
                    recorded_at=datetime.now(UTC) - timedelta(minutes=1),
                    superseded_at=None,
                )
            )
    finally:
        engine.dispose()
