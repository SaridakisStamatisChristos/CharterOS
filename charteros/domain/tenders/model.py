from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.bookings import BookingId
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import QuoteId
from charteros.domain.rfqs import RfqId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, EventId, TypedId


class TenderId(TypedId):
    __slots__ = ()


class TenderInvitationId(TypedId):
    __slots__ = ()


class TenderAdminCorrectionId(TypedId):
    __slots__ = ()


class TenderActorId(TypedId):
    __slots__ = ()


class TenderStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    BEST_AND_FINAL = "best_and_final"
    CLOSED = "closed"
    AWARDED = "awarded"


class TenderInvitationStatus(StrEnum):
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainValidationError(f"{field_name} is required")
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class TenderInvitation:
    id: TenderInvitationId
    tender_id: TenderId
    operator_id: OperatorId
    rfq_id: RfqId
    status: TenderInvitationStatus
    invited_at: datetime
    responded_at: datetime | None = None
    last_quote_id: QuoteId | None = None
    best_and_final_quote_id: QuoteId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "invited_at", _utc(self.invited_at, field_name="invited_at"))
        if self.responded_at is not None:
            responded = _utc(self.responded_at, field_name="responded_at")
            if responded < self.invited_at:
                raise DomainValidationError("responded_at cannot precede invited_at")
            object.__setattr__(self, "responded_at", responded)
        if self.status is TenderInvitationStatus.INVITED and self.responded_at is not None:
            raise DomainValidationError("unanswered invitation cannot have responded_at")
        if self.status is not TenderInvitationStatus.INVITED and self.responded_at is None:
            raise DomainValidationError("answered invitation requires responded_at")
        if (
            self.best_and_final_quote_id is not None
            and self.last_quote_id != self.best_and_final_quote_id
        ):
            raise DomainValidationError("best-and-final quote must be the latest quote")


@dataclass(frozen=True, slots=True)
class TenderAdminCorrection:
    id: TenderAdminCorrectionId
    tender_id: TenderId
    actor_id: TenderActorId
    target_type: str
    target_id: TypedId
    field_name: str
    original_value: object
    replacement_value: object
    reason: str
    corrected_at: datetime
    causation_event_id: EventId

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_type",
            _canonical_text(self.target_type, field_name="target_type", max_length=64),
        )
        object.__setattr__(
            self,
            "field_name",
            _canonical_text(self.field_name, field_name="field_name", max_length=128),
        )
        object.__setattr__(
            self,
            "reason",
            _canonical_text(self.reason, field_name="reason", max_length=1000),
        )
        object.__setattr__(
            self,
            "corrected_at",
            _utc(self.corrected_at, field_name="corrected_at"),
        )
        if self.original_value == self.replacement_value:
            raise DomainValidationError("admin correction must change the interpreted value")


class Tender(AggregateRoot[TenderId]):
    aggregate_type = "tender"

    def __init__(
        self,
        tender_id: TenderId,
        *,
        mission_id: MissionId,
        status: TenderStatus,
        sealed_bid: bool,
        opens_at: datetime,
        deadline_at: datetime,
        created_at: datetime,
        opened_at: datetime | None = None,
        best_and_final_requested_at: datetime | None = None,
        closed_at: datetime | None = None,
        awarded_quote_id: QuoteId | None = None,
        booking_id: BookingId | None = None,
        awarded_at: datetime | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(tender_id, version=version)
        self.mission_id = mission_id
        self.status = TenderStatus(status)
        self.sealed_bid = bool(sealed_bid)
        self.opens_at = _utc(opens_at, field_name="opens_at")
        self.deadline_at = _utc(deadline_at, field_name="deadline_at")
        self.created_at = _utc(created_at, field_name="created_at")
        self.opened_at = _utc(opened_at, field_name="opened_at") if opened_at else None
        self.best_and_final_requested_at = (
            _utc(best_and_final_requested_at, field_name="best_and_final_requested_at")
            if best_and_final_requested_at
            else None
        )
        self.closed_at = _utc(closed_at, field_name="closed_at") if closed_at else None
        self.awarded_quote_id = awarded_quote_id
        self.booking_id = booking_id
        self.awarded_at = _utc(awarded_at, field_name="awarded_at") if awarded_at else None
        self._validate_state()

    def _validate_state(self) -> None:
        if self.opens_at >= self.deadline_at:
            raise DomainValidationError("tender opens_at must precede deadline_at")
        if self.created_at >= self.deadline_at:
            raise DomainValidationError("tender must be created before its deadline")

        if self.status is TenderStatus.DRAFT:
            if any(
                value is not None
                for value in (
                    self.opened_at,
                    self.best_and_final_requested_at,
                    self.closed_at,
                    self.awarded_quote_id,
                    self.booking_id,
                    self.awarded_at,
                )
            ):
                raise DomainValidationError("draft tender has inconsistent lifecycle state")
            return

        if self.opened_at is None:
            raise DomainValidationError("non-draft tender requires opened_at")
        if self.opened_at < self.opens_at or self.opened_at >= self.deadline_at:
            raise DomainValidationError("opened_at must fall inside the tender window")

        if self.status is TenderStatus.OPEN:
            if any(
                value is not None
                for value in (
                    self.best_and_final_requested_at,
                    self.closed_at,
                    self.awarded_quote_id,
                    self.booking_id,
                    self.awarded_at,
                )
            ):
                raise DomainValidationError("open tender has inconsistent lifecycle state")
            return

        if self.status is TenderStatus.BEST_AND_FINAL:
            if self.best_and_final_requested_at is None:
                raise DomainValidationError("best-and-final tender requires request timestamp")
            if not (self.opened_at <= self.best_and_final_requested_at < self.deadline_at):
                raise DomainValidationError(
                    "best_and_final_requested_at must fall inside the tender window"
                )
            if any(
                value is not None
                for value in (
                    self.closed_at,
                    self.awarded_quote_id,
                    self.booking_id,
                    self.awarded_at,
                )
            ):
                raise DomainValidationError(
                    "best-and-final tender has inconsistent lifecycle state"
                )
            return

        if self.closed_at is None or self.closed_at < self.deadline_at:
            raise DomainValidationError("closed tender requires closed_at at or after deadline")
        if (
            self.best_and_final_requested_at is not None
            and self.best_and_final_requested_at >= self.deadline_at
        ):
            raise DomainValidationError("best-and-final request cannot occur at or after deadline")

        if self.status is TenderStatus.CLOSED:
            if any(
                value is not None
                for value in (self.awarded_quote_id, self.booking_id, self.awarded_at)
            ):
                raise DomainValidationError("closed tender cannot already contain award evidence")
            return

        if self.status is TenderStatus.AWARDED:
            if self.awarded_quote_id is None or self.booking_id is None or self.awarded_at is None:
                raise DomainValidationError(
                    "awarded tender requires quote, booking, and award time"
                )
            if self.awarded_at < self.closed_at:
                raise DomainValidationError("awarded_at cannot precede closed_at")

    @classmethod
    def create(
        cls,
        *,
        mission_id: MissionId,
        sealed_bid: bool,
        opens_at: datetime,
        deadline_at: datetime,
        created_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> Tender:
        tender = cls(
            TenderId.new(),
            mission_id=mission_id,
            status=TenderStatus.DRAFT,
            sealed_bid=sealed_bid,
            opens_at=opens_at,
            deadline_at=deadline_at,
            created_at=created_at,
        )
        tender._record_event(
            "TENDER_CREATED",
            {
                "mission_id": str(mission_id),
                "sealed_bid": sealed_bid,
                "opens_at": tender.opens_at.isoformat().replace("+00:00", "Z"),
                "deadline_at": tender.deadline_at.isoformat().replace("+00:00", "Z"),
            },
            correlation_id=correlation_id,
            occurred_at=tender.created_at,
        )
        return tender

    def open(
        self,
        *,
        opened_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not TenderStatus.DRAFT:
            raise DomainValidationError("only a draft tender can be opened")
        when = _utc(opened_at, field_name="opened_at")
        if when < self.opens_at:
            raise DomainValidationError("tender cannot open before opens_at")
        self._assert_supplier_window(when)
        self.status = TenderStatus.OPEN
        self.opened_at = when
        self._record_event(
            "TENDER_OPENED",
            {"opened_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_invitation(
        self,
        *,
        invitation_id: TenderInvitationId,
        operator_id: OperatorId,
        rfq_id: RfqId,
        invited_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        when = _utc(invited_at, field_name="invited_at")
        if self.status not in (TenderStatus.DRAFT, TenderStatus.OPEN):
            raise DomainValidationError("suppliers can only be invited before best-and-final")
        self._assert_supplier_window(when)
        self._record_event(
            "TENDER_SUPPLIER_INVITED",
            {
                "invitation_id": str(invitation_id),
                "operator_id": str(operator_id),
                "rfq_id": str(rfq_id),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_invitation_response(
        self,
        *,
        invitation_id: TenderInvitationId,
        operator_id: OperatorId,
        accepted: bool,
        responded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        when = _utc(responded_at, field_name="responded_at")
        if self.status not in (TenderStatus.OPEN, TenderStatus.BEST_AND_FINAL):
            raise DomainValidationError("tender must be open for supplier participation")
        self._assert_supplier_window(when)
        self._record_event(
            "TENDER_INVITATION_ACCEPTED" if accepted else "TENDER_INVITATION_DECLINED",
            {
                "invitation_id": str(invitation_id),
                "operator_id": str(operator_id),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_bid(
        self,
        *,
        invitation_id: TenderInvitationId,
        operator_id: OperatorId,
        quote_id: QuoteId,
        revision_number: int,
        submitted_at: datetime,
        best_and_final: bool,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        when = _utc(submitted_at, field_name="submitted_at")
        if best_and_final:
            if self.status is not TenderStatus.BEST_AND_FINAL:
                raise DomainValidationError("best-and-final bid requires best-and-final phase")
        elif self.status is not TenderStatus.OPEN:
            raise DomainValidationError("ordinary bid requires open tender phase")
        self._assert_supplier_window(when)
        if revision_number < 1:
            raise DomainValidationError("revision_number must be positive")
        self._record_event(
            "TENDER_BEST_AND_FINAL_SUBMITTED" if best_and_final else "TENDER_BID_SUBMITTED",
            {
                "invitation_id": str(invitation_id),
                "operator_id": str(operator_id),
                "quote_id": str(quote_id),
                "revision_number": revision_number,
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_bid_withdrawal(
        self,
        *,
        invitation_id: TenderInvitationId,
        operator_id: OperatorId,
        quote_id: QuoteId,
        withdrawn_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        when = _utc(withdrawn_at, field_name="withdrawn_at")
        if self.status not in (TenderStatus.OPEN, TenderStatus.BEST_AND_FINAL):
            raise DomainValidationError("bid withdrawal requires an active tender")
        self._assert_supplier_window(when)
        self._record_event(
            "TENDER_BID_WITHDRAWN",
            {
                "invitation_id": str(invitation_id),
                "operator_id": str(operator_id),
                "quote_id": str(quote_id),
            },
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def request_best_and_final(
        self,
        *,
        requested_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not TenderStatus.OPEN:
            raise DomainValidationError("best-and-final can only be requested from an open tender")
        when = _utc(requested_at, field_name="requested_at")
        self._assert_supplier_window(when)
        self.status = TenderStatus.BEST_AND_FINAL
        self.best_and_final_requested_at = when
        self._record_event(
            "TENDER_BEST_AND_FINAL_REQUESTED",
            {"requested_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def close(
        self,
        *,
        closed_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status not in (TenderStatus.OPEN, TenderStatus.BEST_AND_FINAL):
            raise DomainValidationError("only an active tender can be closed")
        when = _utc(closed_at, field_name="closed_at")
        if when < self.deadline_at:
            raise DomainValidationError("tender cannot close before its authoritative deadline")
        self.status = TenderStatus.CLOSED
        self.closed_at = when
        self._record_event(
            "TENDER_CLOSED",
            {"closed_at": when.isoformat().replace("+00:00", "Z")},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def award(
        self,
        *,
        quote_id: QuoteId,
        booking_id: BookingId,
        awarded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if self.status is not TenderStatus.CLOSED:
            raise DomainValidationError("only a closed tender can be awarded")
        assert self.closed_at is not None
        when = _utc(awarded_at, field_name="awarded_at")
        if when < self.closed_at:
            raise DomainValidationError("award cannot precede tender close")
        self.status = TenderStatus.AWARDED
        self.awarded_quote_id = quote_id
        self.booking_id = booking_id
        self.awarded_at = when
        self._record_event(
            "TENDER_AWARDED",
            {"quote_id": str(quote_id), "booking_id": str(booking_id)},
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_admin_correction(
        self,
        correction: TenderAdminCorrection,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if correction.tender_id != self.id:
            raise DomainValidationError("admin correction belongs to another tender")
        if correction.corrected_at < self.deadline_at:
            raise DomainValidationError(
                "admin correction path is reserved for explicit post-deadline corrections"
            )
        self._record_event(
            "TENDER_ADMIN_CORRECTED",
            {
                "correction_id": str(correction.id),
                "target_type": correction.target_type,
                "target_id": str(correction.target_id),
                "field_name": correction.field_name,
                "original_value": correction.original_value,
                "replacement_value": correction.replacement_value,
                "reason": correction.reason,
            },
            actor_id=correction.actor_id,
            correlation_id=correlation_id,
            causation_id=correction.causation_event_id,
            occurred_at=correction.corrected_at,
        )

    def _assert_supplier_window(self, when: datetime) -> None:
        if when >= self.deadline_at:
            raise DomainValidationError(
                "normal supplier mutation is forbidden at or after the tender deadline"
            )
