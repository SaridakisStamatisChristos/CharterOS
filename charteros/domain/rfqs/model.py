from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class RfqId(TypedId):
    __slots__ = ()


class RfqStatus(StrEnum):
    CREATED = "created"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    QUOTED = "quoted"
    DECLINED = "declined"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > 500:
        raise DomainValidationError("decline_reason cannot exceed 500 characters")
    return normalized


class Rfq(AggregateRoot[RfqId]):
    aggregate_type = "rfq"

    def __init__(
        self,
        rfq_id: RfqId,
        *,
        mission_id: MissionId,
        operator_id: OperatorId,
        status: RfqStatus,
        created_at: datetime,
        response_deadline: datetime | None = None,
        sent_at: datetime | None = None,
        acknowledged_at: datetime | None = None,
        declined_at: datetime | None = None,
        expired_at: datetime | None = None,
        decline_reason: str | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(rfq_id, version=version)
        self.mission_id = mission_id
        self.operator_id = operator_id
        self.status = RfqStatus(status)
        self.created_at = _utc(created_at, field_name="created_at")
        self.response_deadline = (
            _utc(response_deadline, field_name="response_deadline")
            if response_deadline is not None
            else None
        )
        self.sent_at = _utc(sent_at, field_name="sent_at") if sent_at is not None else None
        self.acknowledged_at = (
            _utc(acknowledged_at, field_name="acknowledged_at")
            if acknowledged_at is not None
            else None
        )
        self.declined_at = (
            _utc(declined_at, field_name="declined_at") if declined_at is not None else None
        )
        self.expired_at = (
            _utc(expired_at, field_name="expired_at") if expired_at is not None else None
        )
        self.decline_reason = _canonical_reason(decline_reason)
        self._validate_state()

    def _validate_state(self) -> None:
        if self.status is RfqStatus.CREATED:
            if any(
                value is not None
                for value in (
                    self.response_deadline,
                    self.sent_at,
                    self.acknowledged_at,
                    self.declined_at,
                    self.expired_at,
                )
            ):
                raise DomainValidationError("created RFQ cannot have response timestamps")
            return
        if self.response_deadline is None or self.sent_at is None:
            raise DomainValidationError("sent RFQ states require sent_at and response_deadline")
        if self.sent_at < self.created_at:
            raise DomainValidationError("sent_at cannot precede created_at")
        if self.response_deadline <= self.sent_at:
            raise DomainValidationError("response_deadline must be after sent_at")
        if self.acknowledged_at is not None and (
            self.acknowledged_at < self.sent_at or self.acknowledged_at >= self.response_deadline
        ):
            raise DomainValidationError("acknowledged_at must fall within the response window")
        if self.declined_at is not None and (
            self.declined_at < self.sent_at or self.declined_at >= self.response_deadline
        ):
            raise DomainValidationError("declined_at must fall within the response window")
        if (
            self.declined_at is not None
            and self.acknowledged_at is not None
            and self.declined_at < self.acknowledged_at
        ):
            raise DomainValidationError("declined_at cannot precede acknowledged_at")
        if self.expired_at is not None and self.expired_at < self.response_deadline:
            raise DomainValidationError("expired_at cannot precede response_deadline")
        if self.status is RfqStatus.SENT:
            if any(
                value is not None
                for value in (self.acknowledged_at, self.declined_at, self.expired_at)
            ):
                raise DomainValidationError("sent RFQ cannot have terminal response timestamps")
        elif self.status is RfqStatus.ACKNOWLEDGED and (
            self.acknowledged_at is None
            or self.declined_at is not None
            or self.expired_at is not None
        ):
            raise DomainValidationError("acknowledged RFQ has inconsistent timestamps")
        elif self.status is RfqStatus.QUOTED and (
            self.acknowledged_at is None
            or self.declined_at is not None
            or self.expired_at is not None
        ):
            raise DomainValidationError("quoted RFQ has inconsistent timestamps")
        elif self.status is RfqStatus.DECLINED:
            if self.declined_at is None or self.expired_at is not None:
                raise DomainValidationError("declined RFQ has inconsistent timestamps")
        elif self.status is RfqStatus.EXPIRED and (
            self.expired_at is None or self.declined_at is not None
        ):
            raise DomainValidationError("expired RFQ has inconsistent timestamps")

    @classmethod
    def create(
        cls,
        *,
        mission_id: MissionId,
        operator_id: OperatorId,
        created_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Rfq:
        created = _utc(created_at, field_name="created_at")
        rfq = cls(
            RfqId.new(),
            mission_id=mission_id,
            operator_id=operator_id,
            status=RfqStatus.CREATED,
            created_at=created,
        )
        rfq._record_event(
            "RFQ_CREATED",
            {"mission_id": str(mission_id), "operator_id": str(operator_id)},
            correlation_id=correlation_id,
            occurred_at=created,
        )
        return rfq

    def send(
        self,
        *,
        response_deadline: datetime,
        sent_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not RfqStatus.CREATED:
            raise DomainValidationError("only created RFQs can be sent")
        sent = _utc(sent_at, field_name="sent_at")
        deadline = _utc(response_deadline, field_name="response_deadline")
        if deadline <= sent:
            raise DomainValidationError("response_deadline must be after sent_at")
        self.sent_at = sent
        self.response_deadline = deadline
        self.status = RfqStatus.SENT
        self._record_event(
            "RFQ_SENT",
            {"response_deadline": deadline.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=sent,
        )

    def acknowledge(
        self,
        *,
        acknowledged_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not RfqStatus.SENT:
            raise DomainValidationError("only sent RFQs can be acknowledged")
        when = _utc(acknowledged_at, field_name="acknowledged_at")
        response_deadline = self.response_deadline
        if response_deadline is None:
            raise DomainValidationError("active RFQ requires a response deadline")
        if when >= response_deadline:
            raise DomainValidationError("RFQ cannot be acknowledged at or after its deadline")
        self.acknowledged_at = when
        self.status = RfqStatus.ACKNOWLEDGED
        self._record_event(
            "RFQ_ACKNOWLEDGED",
            {"acknowledged_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def decline(
        self,
        *,
        declined_at: datetime,
        reason: str | None = None,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status not in (RfqStatus.SENT, RfqStatus.ACKNOWLEDGED):
            raise DomainValidationError("only sent or acknowledged RFQs can be declined")
        when = _utc(declined_at, field_name="declined_at")
        response_deadline = self.response_deadline
        if response_deadline is None:
            raise DomainValidationError("active RFQ requires a response deadline")
        if when >= response_deadline:
            raise DomainValidationError("RFQ cannot be declined at or after its deadline")
        self.declined_at = when
        self.decline_reason = _canonical_reason(reason)
        self.status = RfqStatus.DECLINED
        self._record_event(
            "RFQ_DECLINED",
            {
                "declined_at": when.isoformat().replace("+00:00", "Z"),
                "reason": self.decline_reason,
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def expire(
        self,
        *,
        expired_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status not in (RfqStatus.SENT, RfqStatus.ACKNOWLEDGED):
            raise DomainValidationError("only sent or acknowledged RFQs can expire")
        when = _utc(expired_at, field_name="expired_at")
        response_deadline = self.response_deadline
        if response_deadline is None:
            raise DomainValidationError("active RFQ requires a response deadline")
        if when < response_deadline:
            raise DomainValidationError("RFQ cannot expire before its response deadline")
        self.expired_at = when
        self.status = RfqStatus.EXPIRED
        self._record_event(
            "RFQ_EXPIRED",
            {"expired_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def mark_quoted(
        self,
        *,
        quoted_at: datetime,
        quote_id: str,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not RfqStatus.ACKNOWLEDGED:
            raise DomainValidationError("only acknowledged RFQs can be quoted")
        when = _utc(quoted_at, field_name="quoted_at")
        response_deadline = self.response_deadline
        if response_deadline is None:
            raise DomainValidationError("active RFQ requires a response deadline")
        if when >= response_deadline:
            raise DomainValidationError("RFQ cannot be quoted at or after its deadline")
        if not quote_id.strip():
            raise DomainValidationError("quote_id is required")
        self.status = RfqStatus.QUOTED
        self._record_event(
            "RFQ_QUOTED",
            {
                "quote_id": quote_id,
                "quoted_at": when.isoformat().replace("+00:00", "Z"),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )
