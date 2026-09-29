from datetime import UTC, datetime, timedelta

import pytest

from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import (
    ProcurementApproval,
    ProcurementApprovalId,
    ProcurementApprovalStatus,
)
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


def test_procurement_approval_supersession_and_consumption_are_explicit() -> None:
    approved_at = datetime.now(UTC)
    buyer_id = OrganizationId.new()
    mission_id = MissionId.new()
    first = ProcurementApproval.create(
        mission_id=mission_id,
        buyer_id=buyer_id,
        quote_id=QuoteId.new(),
        approved_at=approved_at,
        note="Procurement committee approved",
        supersedes_approval_id=None,
        correlation_id=CorrelationId.new(),
    )
    assert [event.event_type for event in first.pending_events] == ["PROCUREMENT_QUOTE_APPROVED"]

    replacement_id = ProcurementApprovalId.new()
    first.supersede(
        replacement_approval_id=replacement_id,
        superseded_at=approved_at + timedelta(minutes=1),
        correlation_id=CorrelationId.new(),
    )
    assert first.status is ProcurementApprovalStatus.SUPERSEDED
    assert first.superseded_at is not None
    assert first.pending_events[-1].event_type == "PROCUREMENT_APPROVAL_SUPERSEDED"

    active = ProcurementApproval.create(
        mission_id=mission_id,
        buyer_id=buyer_id,
        quote_id=QuoteId.new(),
        approved_at=approved_at + timedelta(minutes=2),
        note=None,
        supersedes_approval_id=first.id,
        correlation_id=CorrelationId.new(),
    )
    booking_id = BookingId.new()
    active.consume(
        booking_id=booking_id,
        consumed_at=approved_at + timedelta(minutes=3),
        correlation_id=CorrelationId.new(),
    )
    assert active.status is ProcurementApprovalStatus.CONSUMED
    assert active.booking_id == booking_id
    assert active.pending_events[-1].event_type == "PROCUREMENT_APPROVAL_CONSUMED"


def test_procurement_approval_rejects_invalid_terminal_transitions() -> None:
    approved_at = datetime.now(UTC)
    approval = ProcurementApproval.create(
        mission_id=MissionId.new(),
        buyer_id=OrganizationId.new(),
        quote_id=QuoteId.new(),
        approved_at=approved_at,
        note=None,
        supersedes_approval_id=None,
        correlation_id=CorrelationId.new(),
    )
    with pytest.raises(DomainValidationError):
        approval.consume(
            booking_id=BookingId.new(),
            consumed_at=approved_at - timedelta(seconds=1),
            correlation_id=CorrelationId.new(),
        )

    approval.consume(
        booking_id=BookingId.new(),
        consumed_at=approved_at + timedelta(seconds=1),
        correlation_id=CorrelationId.new(),
    )
    with pytest.raises(DomainValidationError):
        approval.supersede(
            replacement_approval_id=ProcurementApprovalId.new(),
            superseded_at=approved_at + timedelta(seconds=2),
            correlation_id=CorrelationId.new(),
        )
