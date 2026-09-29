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
        ProcurementApprovalRow,
    )
    from charteros.infrastructure.db.models.contracts import ContractRow
    from charteros.infrastructure.db.models.fleet import (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
    )
    from charteros.infrastructure.db.models.graph import (
        GraphAggregateCursorRow,
        GraphEdgeRow,
        GraphNodeRow,
        GraphProjectionCheckpointRow,
        GraphProjectionVersionRow,
    )
    from charteros.infrastructure.db.models.matching import MatchingReferenceProfileRow
    from charteros.infrastructure.db.models.missions import MissionRow
    from charteros.infrastructure.db.models.outbox import OutboxConsumerReceiptRow
    from charteros.infrastructure.db.models.procurement_approvals import ProcurementApprovalRow
    from charteros.infrastructure.db.models.quotes import QuotePriceComponentRow, QuoteRow
    from charteros.infrastructure.db.models.rfqs import RfqRow
    from charteros.infrastructure.db.models.tenders import (
        TenderAdminCorrectionRow,
        TenderInvitationRow,
        TenderRow,
    )

    _ = (
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
        AircraftRow,
        AircraftTypeRow,
        AirportRow,
        BookingRow,
        ContractRow,
        GraphAggregateCursorRow,
        GraphEdgeRow,
        GraphNodeRow,
        GraphProjectionCheckpointRow,
        GraphProjectionVersionRow,
        IdempotencyRecordRow,
        MatchingReferenceProfileRow,
        MissionRow,
        OperatorRow,
        OrganizationRow,
        OutboxConsumerReceiptRow,
        OutboxEventRow,
        QuotePriceComponentRow,
        QuoteRow,
        RfqRow,
        TenderAdminCorrectionRow,
        TenderInvitationRow,
        TenderRow,
    )


__all__ = ["load_models"]
