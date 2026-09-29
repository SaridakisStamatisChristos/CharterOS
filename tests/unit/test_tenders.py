from datetime import UTC, datetime, timedelta

import pytest

from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId
from charteros.domain.tenders import (
    Tender,
    TenderActorId,
    TenderAdminCorrection,
    TenderAdminCorrectionId,
    TenderId,
    TenderInvitationId,
    TenderStatus,
)


def _window() -> tuple[datetime, datetime, datetime]:
    created = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
    opens = created + timedelta(minutes=5)
    deadline = created + timedelta(hours=2)
    return created, opens, deadline


def _tender() -> Tender:
    created, opens, deadline = _window()
    return Tender.create(
        mission_id=MissionId.new(),
        sealed_bid=True,
        opens_at=opens,
        deadline_at=deadline,
        created_at=created,
        correlation_id=CorrelationId.new(),
    )


def test_tender_state_machine_enforces_authoritative_window_and_bafo() -> None:
    created, opens, deadline = _window()
    tender = _tender()

    with pytest.raises(DomainValidationError, match="before opens_at"):
        tender.open(opened_at=created + timedelta(minutes=1))

    tender.open(opened_at=opens)
    assert tender.status is TenderStatus.OPEN

    invitation_id = TenderInvitationId.new()
    operator_id = OperatorId.new()
    rfq_id = RfqId.new()
    tender.record_invitation(
        invitation_id=invitation_id,
        operator_id=operator_id,
        rfq_id=rfq_id,
        invited_at=opens + timedelta(minutes=1),
    )
    tender.record_invitation_response(
        invitation_id=invitation_id,
        operator_id=operator_id,
        accepted=True,
        responded_at=opens + timedelta(minutes=2),
    )
    tender.record_bid(
        invitation_id=invitation_id,
        operator_id=operator_id,
        quote_id=QuoteId.new(),
        revision_number=1,
        submitted_at=opens + timedelta(minutes=3),
        best_and_final=False,
    )

    tender.request_best_and_final(requested_at=opens + timedelta(minutes=30))
    assert tender.status is TenderStatus.BEST_AND_FINAL

    with pytest.raises(DomainValidationError, match="ordinary bid"):
        tender.record_bid(
            invitation_id=invitation_id,
            operator_id=operator_id,
            quote_id=QuoteId.new(),
            revision_number=2,
            submitted_at=opens + timedelta(minutes=31),
            best_and_final=False,
        )

    tender.record_bid(
        invitation_id=invitation_id,
        operator_id=operator_id,
        quote_id=QuoteId.new(),
        revision_number=2,
        submitted_at=deadline - timedelta(seconds=1),
        best_and_final=True,
    )

    with pytest.raises(DomainValidationError, match="forbidden"):
        tender.record_bid(
            invitation_id=invitation_id,
            operator_id=operator_id,
            quote_id=QuoteId.new(),
            revision_number=3,
            submitted_at=deadline,
            best_and_final=True,
        )

    with pytest.raises(DomainValidationError, match="cannot close before"):
        tender.close(closed_at=deadline - timedelta(microseconds=1))

    tender.close(closed_at=deadline)
    assert tender.status is TenderStatus.CLOSED
    tender.award(
        quote_id=QuoteId.new(),
        booking_id=BookingId.new(),
        awarded_at=deadline + timedelta(seconds=1),
    )
    assert tender.status is TenderStatus.AWARDED


def test_post_deadline_admin_correction_is_append_only_auditable_event() -> None:
    _, opens, deadline = _window()
    tender = _tender()
    tender.open(opened_at=opens)
    tender.close(closed_at=deadline)
    causation = EventId.new()
    actor = TenderActorId.new()
    correction = TenderAdminCorrection(
        id=TenderAdminCorrectionId.new(),
        tender_id=tender.id,
        actor_id=actor,
        target_type="quote",
        target_id=TypedId.new(),
        field_name="payment_terms",
        original_value="50% on confirmation",
        replacement_value="50% on contract",
        reason="Correct transcription error from signed supplier evidence",
        corrected_at=deadline + timedelta(minutes=1),
        causation_event_id=causation,
    )

    original_status = tender.status
    original_deadline = tender.deadline_at
    tender.record_admin_correction(correction, correlation_id=CorrelationId.new())

    assert tender.status is original_status
    assert tender.deadline_at == original_deadline
    event = tender.pending_events[-1]
    assert event.event_type == "TENDER_ADMIN_CORRECTED"
    assert event.actor_id == actor
    assert event.causation_id == causation
    assert event.payload["original_value"] == "50% on confirmation"
    assert event.payload["replacement_value"] == "50% on contract"


def test_admin_correction_before_deadline_is_rejected() -> None:
    _, opens, deadline = _window()
    tender = _tender()
    tender.open(opened_at=opens)
    correction = TenderAdminCorrection(
        id=TenderAdminCorrectionId.new(),
        tender_id=tender.id,
        actor_id=TenderActorId.new(),
        target_type="tender",
        target_id=TypedId(tender.id.value),
        field_name="deadline_at",
        original_value=deadline.isoformat(),
        replacement_value=(deadline + timedelta(minutes=5)).isoformat(),
        reason="test correction",
        corrected_at=deadline - timedelta(seconds=1),
        causation_event_id=EventId.new(),
    )
    with pytest.raises(DomainValidationError, match="post-deadline"):
        tender.record_admin_correction(correction)


def test_admin_correction_must_change_interpreted_value() -> None:
    _, _, deadline = _window()
    with pytest.raises(DomainValidationError, match="must change"):
        TenderAdminCorrection(
            id=TenderAdminCorrectionId.new(),
            tender_id=TenderId.new(),
            actor_id=TenderActorId.new(),
            target_type="quote",
            target_id=TypedId.new(),
            field_name="payment_terms",
            original_value="unchanged",
            replacement_value="unchanged",
            reason="No-op corrections are not valid evidence",
            corrected_at=deadline + timedelta(minutes=1),
            causation_event_id=EventId.new(),
        )
