from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import DomainEventRepository, OperatorRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.application.ports.tenders import TenderRepository
from charteros.domain.missions import MissionId, MissionStatus
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.rfqs import Rfq, RfqId, RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class RfqService:
    def __init__(
        self,
        *,
        rfqs: RfqRepository,
        missions: MissionRepository,
        operators: OperatorRepository,
        events: DomainEventRepository,
        tenders: TenderRepository | None = None,
    ) -> None:
        self._rfqs = rfqs
        self._missions = missions
        self._operators = operators
        self._events = events
        self._tenders = tenders

    def create_and_send(
        self,
        *,
        mission_id: MissionId,
        operator_id: OperatorId,
        response_deadline: datetime,
        now: datetime,
        correlation_id: CorrelationId,
        tender_command: bool = False,
    ) -> Rfq:
        issued_at = _utc(now, field_name="now")
        deadline = _utc(response_deadline, field_name="response_deadline")
        mission = self._missions.get_for_update(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")
        if mission.status not in (MissionStatus.OPEN, MissionStatus.SOURCING):
            raise EntityConflictError("RFQs can only be created for open or sourcing missions")
        if (
            self._tenders is not None
            and not tender_command
            and self._tenders.get_for_mission(mission_id) is not None
        ):
            raise EntityConflictError(
                "RFQs for a tender mission must be created through the tender invitation workflow"
            )
        if deadline <= issued_at:
            raise EntityConflictError("RFQ response_deadline must be in the future")
        if deadline >= mission.departure_window.start:
            raise EntityConflictError("RFQ response_deadline must precede mission departure")

        operator = self._operators.get(operator_id)
        if operator is None:
            raise EntityNotFoundError("target operator does not exist")
        if (
            operator.verification_status is not VerificationStatus.VERIFIED
            or operator.insurance_status is not InsuranceStatus.VALID
            or operator.commercial_status is not CommercialStatus.ACTIVE
        ):
            raise EntityConflictError(
                "target operator must be verified, currently insured, and commercially active"
            )
        if self._rfqs.find_for_mission_operator(mission_id, operator_id) is not None:
            raise EntityConflictError("an RFQ already exists for this mission and operator")

        if mission.status is MissionStatus.OPEN:
            expected_mission_version = mission.version
            mission.start_sourcing(recorded_at=issued_at, correlation_id=correlation_id)
            self._missions.save(mission, expected_version=expected_mission_version)
            self._events.add_aggregate_events(mission)

        rfq = Rfq.create(
            mission_id=mission_id,
            operator_id=operator_id,
            created_at=issued_at,
            correlation_id=correlation_id,
        )
        rfq.send(
            response_deadline=deadline,
            sent_at=issued_at,
            correlation_id=correlation_id,
        )
        self._rfqs.add(rfq)
        self._events.add_aggregate_events(rfq)
        return rfq

    def list_for_mission(self, mission_id: MissionId) -> tuple[Rfq, ...]:
        if self._missions.get(mission_id) is None:
            raise EntityNotFoundError("mission does not exist")
        return self._rfqs.list_for_mission(mission_id)

    def acknowledge(
        self,
        *,
        rfq_id: RfqId,
        now: datetime,
        correlation_id: CorrelationId,
        tender_command: bool = False,
    ) -> Rfq:
        when = _utc(now, field_name="now")
        rfq = self._get_for_update(rfq_id)
        self._assert_tender_command(rfq.id, tender_command=tender_command)
        if rfq.status is not RfqStatus.SENT:
            raise EntityConflictError("only sent RFQs can be acknowledged")
        response_deadline = rfq.response_deadline
        if response_deadline is None:
            raise EntityConflictError("RFQ response deadline is missing")
        if when >= response_deadline:
            raise EntityConflictError("RFQ response deadline has passed")
        expected_version = rfq.version
        rfq.acknowledge(acknowledged_at=when, correlation_id=correlation_id)
        self._rfqs.save(rfq, expected_version=expected_version)
        self._events.add_aggregate_events(rfq)
        return rfq

    def decline(
        self,
        *,
        rfq_id: RfqId,
        reason: str | None,
        now: datetime,
        correlation_id: CorrelationId,
        tender_command: bool = False,
    ) -> Rfq:
        when = _utc(now, field_name="now")
        rfq = self._get_for_update(rfq_id)
        self._assert_tender_command(rfq.id, tender_command=tender_command)
        if rfq.status not in (RfqStatus.SENT, RfqStatus.ACKNOWLEDGED):
            raise EntityConflictError("only sent or acknowledged RFQs can be declined")
        response_deadline = rfq.response_deadline
        if response_deadline is None:
            raise EntityConflictError("RFQ response deadline is missing")
        if when >= response_deadline:
            raise EntityConflictError("RFQ response deadline has passed")
        expected_version = rfq.version
        rfq.decline(
            declined_at=when,
            reason=reason,
            correlation_id=correlation_id,
        )
        self._rfqs.save(rfq, expected_version=expected_version)
        self._events.add_aggregate_events(rfq)
        return rfq

    def expire(
        self,
        *,
        rfq_id: RfqId,
        now: datetime,
        correlation_id: CorrelationId,
        tender_command: bool = False,
    ) -> Rfq:
        when = _utc(now, field_name="now")
        rfq = self._get_for_update(rfq_id)
        self._assert_tender_command(rfq.id, tender_command=tender_command)
        if rfq.status not in (RfqStatus.SENT, RfqStatus.ACKNOWLEDGED):
            raise EntityConflictError("only sent or acknowledged RFQs can expire")
        response_deadline = rfq.response_deadline
        if response_deadline is None:
            raise EntityConflictError("RFQ response deadline is missing")
        if when < response_deadline:
            raise EntityConflictError("RFQ response deadline has not passed")
        expected_version = rfq.version
        rfq.expire(expired_at=when, correlation_id=correlation_id)
        self._rfqs.save(rfq, expected_version=expected_version)
        self._events.add_aggregate_events(rfq)
        return rfq

    def _assert_tender_command(self, rfq_id: RfqId, *, tender_command: bool) -> None:
        if self._tenders is None or tender_command:
            return
        if self._tenders.find_invitation_for_rfq(rfq_id) is not None:
            raise EntityConflictError(
                "tender invitations must be answered through the tender participation workflow"
            )

    def _get_for_update(self, rfq_id: RfqId) -> Rfq:
        rfq = self._rfqs.get_for_update(rfq_id)
        if rfq is None:
            raise EntityNotFoundError("RFQ does not exist")
        return rfq
