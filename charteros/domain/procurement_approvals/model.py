from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class ProcurementApprovalId(TypedId):
    __slots__ = ()


class ProcurementApprovalStatus(StrEnum):
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    CONSUMED = "consumed"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > 1000:
        raise DomainValidationError("approval note cannot exceed 1000 characters")
    return normalized


class ProcurementApproval(AggregateRoot[ProcurementApprovalId]):
    """Buyer pre-award approval evidence bound to one immutable Quote revision."""

    aggregate_type = "procurement_approval"

    def __init__(
        self,
        approval_id: ProcurementApprovalId,
        *,
        mission_id: MissionId,
        buyer_id: OrganizationId,
        quote_id: QuoteId,
        status: ProcurementApprovalStatus,
        approved_at: datetime,
        note: str | None = None,
        supersedes_approval_id: ProcurementApprovalId | None = None,
        superseded_at: datetime | None = None,
        consumed_at: datetime | None = None,
        booking_id: BookingId | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(approval_id, version=version)
        self.mission_id = mission_id
        self.buyer_id = buyer_id
        self.quote_id = quote_id
        self.status = ProcurementApprovalStatus(status)
        self.approved_at = _utc(approved_at, field_name="approved_at")
        self.note = _note(note)
        self.supersedes_approval_id = supersedes_approval_id
        self.superseded_at = (
            _utc(superseded_at, field_name="superseded_at") if superseded_at is not None else None
        )
        self.consumed_at = (
            _utc(consumed_at, field_name="consumed_at") if consumed_at is not None else None
        )
        self.booking_id = booking_id
        self._validate_state()

    def _validate_state(self) -> None:
        if self.supersedes_approval_id == self.id:
            raise DomainValidationError("approval cannot supersede itself")
        if self.superseded_at is not None and self.superseded_at < self.approved_at:
            raise DomainValidationError("superseded_at cannot precede approved_at")
        if self.consumed_at is not None and self.consumed_at < self.approved_at:
            raise DomainValidationError("consumed_at cannot precede approved_at")
        if self.status is ProcurementApprovalStatus.APPROVED:
            if (
                self.superseded_at is not None
                or self.consumed_at is not None
                or self.booking_id is not None
            ):
                raise DomainValidationError("approved procurement approval has terminal evidence")
        elif self.status is ProcurementApprovalStatus.SUPERSEDED:
            if (
                self.superseded_at is None
                or self.consumed_at is not None
                or self.booking_id is not None
            ):
                raise DomainValidationError("superseded procurement approval has invalid evidence")
        elif self.status is ProcurementApprovalStatus.CONSUMED and (
            self.consumed_at is None or self.booking_id is None or self.superseded_at is not None
        ):
            raise DomainValidationError("consumed procurement approval has invalid evidence")

    @classmethod
    def create(
        cls,
        *,
        mission_id: MissionId,
        buyer_id: OrganizationId,
        quote_id: QuoteId,
        approved_at: datetime,
        note: str | None,
        supersedes_approval_id: ProcurementApprovalId | None,
        correlation_id: CorrelationId | None = None,
    ) -> ProcurementApproval:
        approval = cls(
            ProcurementApprovalId.new(),
            mission_id=mission_id,
            buyer_id=buyer_id,
            quote_id=quote_id,
            status=ProcurementApprovalStatus.APPROVED,
            approved_at=approved_at,
            note=note,
            supersedes_approval_id=supersedes_approval_id,
        )
        approval._record_event(
            "PROCUREMENT_QUOTE_APPROVED",
            {
                "mission_id": str(mission_id),
                "buyer_id": str(buyer_id),
                "quote_id": str(quote_id),
                "approved_at": approval.approved_at.isoformat().replace("+00:00", "Z"),
                "supersedes_approval_id": (
                    str(supersedes_approval_id) if supersedes_approval_id is not None else None
                ),
                "note": approval.note,
            },
            actor_id=buyer_id,
            correlation_id=correlation_id,
            occurred_at=approval.approved_at,
        )
        return approval

    def supersede(
        self,
        *,
        replacement_approval_id: ProcurementApprovalId,
        superseded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not ProcurementApprovalStatus.APPROVED:
            raise DomainValidationError("only an active approval can be superseded")
        if replacement_approval_id == self.id:
            raise DomainValidationError("replacement approval must differ from current approval")
        when = _utc(superseded_at, field_name="superseded_at")
        if when < self.approved_at:
            raise DomainValidationError("superseded_at cannot precede approved_at")
        self.status = ProcurementApprovalStatus.SUPERSEDED
        self.superseded_at = when
        self._record_event(
            "PROCUREMENT_APPROVAL_SUPERSEDED",
            {
                "replacement_approval_id": str(replacement_approval_id),
                "superseded_at": when.isoformat().replace("+00:00", "Z"),
            },
            actor_id=self.buyer_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def consume(
        self,
        *,
        booking_id: BookingId,
        consumed_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not ProcurementApprovalStatus.APPROVED:
            raise DomainValidationError("only an active approval can be consumed")
        when = _utc(consumed_at, field_name="consumed_at")
        if when < self.approved_at:
            raise DomainValidationError("consumed_at cannot precede approved_at")
        self.status = ProcurementApprovalStatus.CONSUMED
        self.consumed_at = when
        self.booking_id = booking_id
        self._record_event(
            "PROCUREMENT_APPROVAL_CONSUMED",
            {
                "booking_id": str(booking_id),
                "consumed_at": when.isoformat().replace("+00:00", "Z"),
            },
            actor_id=self.buyer_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )
