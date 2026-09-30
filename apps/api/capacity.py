from sqlalchemy.orm import Session

from charteros.application.capacity import AircraftCapacityPolicy
from charteros.infrastructure.db.repositories.capacity import (
    SqlAlchemyAircraftCapacityReservationRepository,
    SqlAlchemyCapacityReferenceRepository,
)
from charteros.infrastructure.db.repositories.catalog import (
    SqlAlchemyAircraftRepository,
    SqlAlchemyAirportRepository,
)


def build_capacity_policy(session: Session) -> AircraftCapacityPolicy:
    return AircraftCapacityPolicy(
        aircraft=SqlAlchemyAircraftRepository(session),
        airports=SqlAlchemyAirportRepository(session),
        references=SqlAlchemyCapacityReferenceRepository(session),
    )


def build_capacity_reservations(
    session: Session,
) -> SqlAlchemyAircraftCapacityReservationRepository:
    return SqlAlchemyAircraftCapacityReservationRepository(session)
