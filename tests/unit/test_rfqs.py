from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import Rfq, RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=4)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _sent_rfq() -> Rfq:
    rfq = Rfq.create(
        mission_id=MissionId(_id(1)),
        operator_id=OperatorId(_id(2)),
        created_at=NOW,
        correlation_id=CorrelationId(_id(3)),
    )
    rfq.send(
        response_deadline=DEADLINE,
        sent_at=NOW,
        correlation_id=CorrelationId(_id(3)),
    )
    return rfq


def test_create_and_send_emit_explicit_events() -> None:
    rfq = _sent_rfq()

    assert rfq.status is RfqStatus.SENT
    assert rfq.version == 2
    assert rfq.response_deadline == DEADLINE
    assert [event.event_type for event in rfq.pending_events] == [
        "RFQ_CREATED",
        "RFQ_SENT",
    ]


def test_acknowledge_is_valid_only_inside_response_window() -> None:
    rfq = _sent_rfq()
    rfq.acknowledge(
        acknowledged_at=NOW + timedelta(hours=1),
        correlation_id=CorrelationId(_id(4)),
    )

    assert rfq.status is RfqStatus.ACKNOWLEDGED
    assert rfq.version == 3
    assert rfq.acknowledged_at == NOW + timedelta(hours=1)
    assert rfq.pending_events[-1].event_type == "RFQ_ACKNOWLEDGED"

    with pytest.raises(DomainValidationError, match="only sent"):
        rfq.acknowledge(acknowledged_at=NOW + timedelta(hours=2))


def test_decline_after_acknowledgement_preserves_lineage() -> None:
    rfq = _sent_rfq()
    rfq.acknowledge(acknowledged_at=NOW + timedelta(minutes=30))
    rfq.decline(
        declined_at=NOW + timedelta(hours=1),
        reason="  Aircraft   unavailable  ",
    )

    assert rfq.status is RfqStatus.DECLINED
    assert rfq.version == 4
    assert rfq.decline_reason == "Aircraft unavailable"
    assert rfq.acknowledged_at == NOW + timedelta(minutes=30)
    assert rfq.pending_events[-1].event_type == "RFQ_DECLINED"


def test_expiry_requires_deadline_to_have_arrived() -> None:
    rfq = _sent_rfq()

    with pytest.raises(DomainValidationError, match="before its response deadline"):
        rfq.expire(expired_at=DEADLINE - timedelta(seconds=1))

    rfq.expire(expired_at=DEADLINE)
    assert rfq.status is RfqStatus.EXPIRED
    assert rfq.version == 3
    assert rfq.expired_at == DEADLINE
    assert rfq.pending_events[-1].event_type == "RFQ_EXPIRED"


def test_response_actions_at_deadline_are_rejected() -> None:
    rfq = _sent_rfq()
    with pytest.raises(DomainValidationError, match="at or after"):
        rfq.acknowledge(acknowledged_at=DEADLINE)

    rfq = _sent_rfq()
    with pytest.raises(DomainValidationError, match="at or after"):
        rfq.decline(declined_at=DEADLINE)


def test_naive_timestamps_and_invalid_reconstruction_are_rejected() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        Rfq.create(
            mission_id=MissionId(_id(1)),
            operator_id=OperatorId(_id(2)),
            created_at=datetime(2026, 9, 28, 12),
        )

    with pytest.raises(DomainValidationError, match="sent_at cannot precede"):
        Rfq(
            _sent_rfq().id,
            mission_id=MissionId(_id(1)),
            operator_id=OperatorId(_id(2)),
            status=RfqStatus.SENT,
            created_at=NOW,
            sent_at=NOW - timedelta(minutes=1),
            response_deadline=DEADLINE,
            version=2,
        )
