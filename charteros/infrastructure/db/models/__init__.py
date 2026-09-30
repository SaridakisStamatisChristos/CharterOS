def load_models() -> None:
    """Import all ORM models so SQLAlchemy metadata is complete."""
    from charteros.infrastructure.db.models.abuse import ApiRateLimitWindowRow
    from charteros.infrastructure.db.models.bookings import BookingRow
    from charteros.infrastructure.db.models.capacity import AircraftCapacityReservationRow
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
    from charteros.infrastructure.db.models.disruptions import (
        DisruptionBuyerDecisionRow,
        DisruptionCommercialChangeRow,
        DisruptionProposalRow,
        DisruptionRow,
    )
    from charteros.infrastructure.db.models.evidence import DecisionEvidenceSnapshotRow
    from charteros.infrastructure.db.models.evidence_integrity import (
        EvidenceIntegrityCheckpointRow,
        EvidenceIntegrityEntryRow,
    )
    from charteros.infrastructure.db.models.fleet import (
        ApiRateLimitWindowRow,
        AircraftAvailabilityRecordRow,
        AircraftPositionObservationRow,
    )
    from charteros.infrastructure.db.models.fx import FxLockConversionRow, FxLockRow, FxRateRow
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
    from charteros.infrastructure.db.models.reconciliation import (
        FinancialReconciliationRow,
        OperatorInvoiceLineRow,
        OperatorInvoiceRevisionRow,
        ReconciliationDisputeRow,
        VarianceApprovalRow,
    )
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
        AircraftCapacityReservationRow,
        AircraftTypeRow,
        AirportRow,
        BookingRow,
        ContractRow,
        DisruptionBuyerDecisionRow,
        DisruptionCommercialChangeRow,
        DisruptionProposalRow,
        DisruptionRow,
        DecisionEvidenceSnapshotRow,
        EvidenceIntegrityCheckpointRow,
        EvidenceIntegrityEntryRow,
        FinancialReconciliationRow,
        FxLockConversionRow,
        FxLockRow,
        FxRateRow,
        GraphAggregateCursorRow,
        GraphEdgeRow,
        GraphNodeRow,
        GraphProjectionCheckpointRow,
        GraphProjectionVersionRow,
        IdempotencyRecordRow,
        MatchingReferenceProfileRow,
        MissionRow,
        OperatorInvoiceLineRow,
        OperatorInvoiceRevisionRow,
        OperatorRow,
        OrganizationRow,
        OutboxConsumerReceiptRow,
        OutboxEventRow,
        ProcurementApprovalRow,
        QuotePriceComponentRow,
        QuoteRow,
        ReconciliationDisputeRow,
        RfqRow,
        TenderAdminCorrectionRow,
        TenderInvitationRow,
        TenderRow,
        VarianceApprovalRow,
    )


__all__ = ["load_models"]
