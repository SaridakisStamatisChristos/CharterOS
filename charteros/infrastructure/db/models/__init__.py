def load_models() -> None:
    """Import all ORM models so SQLAlchemy metadata is complete."""
    from charteros.infrastructure.db.models.bookings import BookingRow
    from charteros.infrastructure.db.models.catalog import (
        AircraftRow,
        AircraftTypeRow,
        AirportRow,
        IdempotencyRecordRow,
        OperatorRow,
        OrganizationRow,
        OutboxEventRow,
    )
    from charteros.infrastructure.db.models.contracts import ContractRow
    from charteros.infrastructure.db.models.fleet import (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
    )
    from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
    from charteros.infrastructure.db.models.missions import MissionRow
    from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
    from charteros.infrastructure.db.models.quotes import QuotePriceComponentRow, QuoteRow
    from charteros.infrastructure.db.models.rfqs import RfqRow

    _ = (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
        AircraftRow,
        AircraftTypeRow,
        AirportRow,
        BookingRow,
        ContractRow,
        IdempotencyRecordRow,
        MatchingReferenceProfileRow,
        MissionRow,
        OperatorRow,
        OrganizationRow,
        OutboxEventRow,
        QuotePriceComponentRow,
        QuoteRow,
        RfqRow,
    )


__all__ = ["load_models"]
