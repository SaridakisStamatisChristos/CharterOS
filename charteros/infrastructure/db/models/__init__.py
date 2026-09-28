def load_models() -> None:
    """Import all ORM models so SQLAlchemy metadata is complete."""
    from charteros.infrastructure.db.models.catalog import (
        AircraftRow,
        AircraftTypeRow,
        AirportRow,
        IdempotencyRecordRow,
        OperatorRow,
        OrganizationRow,
        OutboxEventRow,
    )
    from charteros.infrastructure.db.models.fleet import (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
    )

    _ = (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
        AircraftRow,
        AircraftTypeRow,
        AirportRow,
        IdempotencyRecordRow,
        OperatorRow,
        OrganizationRow,
        OutboxEventRow,
    )


__all__ = ["load_models"]
