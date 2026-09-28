from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.bookings import BookingId
from charteros.domain.contracts import Contract, ContractId, ContractStatus
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.shared.exceptions import DomainValidationError

NOW = datetime(2025, 9, 29, 10, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _contract() -> Contract:
    return Contract.create(
        booking_id=BookingId(_id(1)),
        buyer_id=OrganizationId(_id(2)),
        operator_id=OperatorId(_id(3)),
        document_reference="  object://contracts/booking-1-v1.pdf  ",
        document_version=1,
        metadata={
            " Jurisdiction ": " GR ",
            "Template": " Group Charter ",
        },
        created_at=NOW,
    )


def test_contract_creation_canonicalizes_document_evidence_and_emits_event() -> None:
    contract = _contract()

    assert contract.status.value == "pending_acceptance"
    assert contract.version == 1
    assert contract.document_reference == "object://contracts/booking-1-v1.pdf"
    assert contract.document_version == 1
    assert dict(contract.metadata) == {
        "Jurisdiction": "GR",
        "Template": "Group Charter",
    }
    assert contract.buyer_signed_at is None
    assert contract.operator_signed_at is None
    assert contract.accepted_at is None

    event = contract.pending_events[-1]
    assert event.event_type == "CONTRACT_CREATED"
    assert event.payload["booking_id"] == str(contract.booking_id)
    assert event.payload["document_version"] == 1


def test_contract_rejects_invalid_document_reference_version_and_metadata() -> None:
    with pytest.raises(DomainValidationError, match="document_reference is required"):
        Contract.create(
            booking_id=BookingId(_id(1)),
            buyer_id=OrganizationId(_id(2)),
            operator_id=OperatorId(_id(3)),
            document_reference="   ",
            document_version=1,
            metadata={},
            created_at=NOW,
        )

    with pytest.raises(DomainValidationError, match="document_version"):
        Contract.create(
            booking_id=BookingId(_id(1)),
            buyer_id=OrganizationId(_id(2)),
            operator_id=OperatorId(_id(3)),
            document_reference="contract.pdf",
            document_version=0,
            metadata={},
            created_at=NOW,
        )

    with pytest.raises(DomainValidationError, match="unique case-insensitively"):
        Contract.create(
            booking_id=BookingId(_id(1)),
            buyer_id=OrganizationId(_id(2)),
            operator_id=OperatorId(_id(3)),
            document_reference="contract.pdf",
            document_version=1,
            metadata={"Template": "A", " template ": "B"},
            created_at=NOW,
        )


def test_bilateral_acceptance_records_each_signature_and_final_acceptance() -> None:
    contract = _contract()
    buyer_time = NOW + timedelta(minutes=5)
    operator_time = NOW + timedelta(minutes=7)

    contract.accept_buyer(signed_at=buyer_time)
    assert contract.status.value == "partially_accepted"
    assert contract.version == 2
    assert contract.buyer_signed_at == buyer_time
    assert contract.operator_signed_at is None
    assert contract.accepted_at is None

    contract.accept_operator(signed_at=operator_time)
    assert contract.status.value == "accepted"
    assert contract.version == 4
    assert contract.operator_signed_at == operator_time
    assert contract.accepted_at == operator_time
    assert [event.event_type for event in contract.pending_events] == [
        "CONTRACT_CREATED",
        "CONTRACT_BUYER_ACCEPTED",
        "CONTRACT_OPERATOR_ACCEPTED",
        "CONTRACT_ACCEPTED",
    ]


def test_operator_can_accept_first_and_final_timestamp_is_later_signature() -> None:
    contract = _contract()
    operator_time = NOW + timedelta(minutes=3)
    buyer_time = NOW + timedelta(minutes=9)

    contract.accept_operator(signed_at=operator_time)
    assert contract.status.value == "partially_accepted"

    contract.accept_buyer(signed_at=buyer_time)
    assert contract.status.value == "accepted"
    assert contract.accepted_at == buyer_time


def test_same_party_acceptance_is_single_use() -> None:
    contract = _contract()
    contract.accept_buyer(signed_at=NOW + timedelta(minutes=1))

    with pytest.raises(DomainValidationError, match="already accepted"):
        contract.accept_buyer(signed_at=NOW + timedelta(minutes=2))


def test_signature_cannot_precede_contract_creation() -> None:
    contract = _contract()

    with pytest.raises(DomainValidationError, match="cannot precede contract creation"):
        contract.accept_operator(signed_at=NOW - timedelta(seconds=1))


def test_rehydration_rejects_inconsistent_acceptance_state() -> None:
    with pytest.raises(DomainValidationError, match="requires both signed timestamps"):
        Contract(
            ContractId(_id(10)),
            booking_id=BookingId(_id(11)),
            buyer_id=OrganizationId(_id(12)),
            operator_id=OperatorId(_id(13)),
            document_reference="contract.pdf",
            document_version=1,
            metadata={},
            status=ContractStatus.ACCEPTED,
            created_at=NOW,
            buyer_signed_at=NOW + timedelta(minutes=1),
            operator_signed_at=None,
            accepted_at=NOW + timedelta(minutes=1),
            version=3,
        )
