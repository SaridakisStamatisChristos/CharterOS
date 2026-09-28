from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.airports import AirportId
from charteros.domain.missions import Mission, MissionId, MissionStatus
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.models.missions import MissionRow


def _to_domain(row: MissionRow) -> Mission:
    budget = None
    if row.max_budget_amount_minor is not None and row.max_budget_currency is not None:
        budget = Money(row.max_budget_amount_minor, Currency(row.max_budget_currency))
    return Mission(
        MissionId(row.id),
        buyer_id=OrganizationId(row.buyer_id),
        origin_airport_id=AirportId(row.origin_airport_id),
        destination_airport_id=AirportId(row.destination_airport_id),
        departure_window=TimeRange(row.departure_from, row.departure_to),
        passenger_count=row.passenger_count,
        max_budget=budget,
        special_requirements=tuple(row.special_requirements),
        status=MissionStatus(row.status),
        version=row.version,
    )


class SqlAlchemyMissionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, mission: Mission) -> None:
        self._session.add(
            MissionRow(
                id=mission.id.value,
                version=mission.version,
                buyer_id=mission.buyer_id.value,
                origin_airport_id=mission.origin_airport_id.value,
                destination_airport_id=mission.destination_airport_id.value,
                departure_from=mission.departure_window.start,
                departure_to=mission.departure_window.end,
                passenger_count=mission.passenger_count,
                max_budget_amount_minor=(
                    mission.max_budget.amount_minor if mission.max_budget is not None else None
                ),
                max_budget_currency=(
                    str(mission.max_budget.currency) if mission.max_budget is not None else None
                ),
                special_requirements=list(mission.special_requirements),
                status=mission.status.value,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError("mission conflicts with persisted state") from exc

    def get(self, mission_id: MissionId) -> Mission | None:
        row = self._session.get(MissionRow, mission_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_update(self, mission_id: MissionId) -> Mission | None:
        row = self._session.scalar(
            select(MissionRow).where(MissionRow.id == mission_id.value).with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def save(self, mission: Mission, *, expected_version: int) -> None:
        statement = (
            update(MissionRow)
            .where(MissionRow.id == mission.id.value, MissionRow.version == expected_version)
            .values(version=mission.version, status=mission.status.value)
            .returning(MissionRow.id)
        )
        updated_id = self._session.scalar(statement)
        if updated_id is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for mission {mission.id}"
            )
        self._session.flush()
