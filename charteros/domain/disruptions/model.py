from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from charteros.domain.aircraft import AircraftId
from charteros.domain.bookings import BookingId, BookingState
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange


class DisruptionId(TypedId):
    __slots__ = ()


class DisruptionProposalId(TypedId):
    __slots__ = ()


class DisruptionCommercialChangeId(TypedId):
    __slots__ = ()


class DisruptionBuyerDecisionId(TypedId):
    __slots__ = ()


class DisruptionType(StrEnum):
    DELAY = "delay"
    AIRCRAFT_UNAVAILABLE = "aircraft_unavailable"
    CREW_UNAVAILABLE = "crew_unavailable"
    AIRPORT_RESTRICTION = "airport_restriction"
    TECHNICAL = "technical"
    WEATHER = "weather"
    OTHER = "other"


class DisruptionStatus(StrEnum):
    OPEN = "open"
    PROPOSED = "proposed"
    AWAITING_BUYER = "awaiting_buyer"
    BUYER_APPROVED = "buyer_approved"
    BUYER_REJECTED = "buyer_rejected"
    RESOLVED = "resolved"


class DisruptionProposalStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class DisruptionCommercialChangeStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class DisruptionBuyerDecisionValue(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise DomainValidationError(f"{field_name} is required")
        return None
    normalized = " ".join(value.split())
    if not normalized:
        if required:
            raise DomainValidationError(f"{field_name} is required")
        return None
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = _text(
        value,
        field_name=field_name,
        max_length=max_length,
        required=True,
    )
    assert normalized is not None
    return normalized


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReplacementProposal:
    id: DisruptionProposalId
    disruption_id: DisruptionId
    revision_number: int
    supersedes_proposal_id: DisruptionProposalId | None
    status: DisruptionProposalStatus
    proposed_operator_id: OperatorId
    proposed_aircraft_id: AircraftId
    departure_window: TimeRange | None
    requires_buyer_decision: bool
    source: str
    source_evidence: str | None
    proposed_at: datetime
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision_number, int) or isinstance(self.revision_number, bool):
            raise DomainValidationError("proposal revision_number must be an integer")
        if self.revision_number < 1:
            raise DomainValidationError("proposal revision_number must be positive")
        if self.revision_number == 1 and self.supersedes_proposal_id is not None:
            raise DomainValidationError("initial disruption proposal cannot supersede another")
        if self.revision_number > 1 and self.supersedes_proposal_id is None:
            raise DomainValidationError("revised disruption proposal must identify its predecessor")
        if self.supersedes_proposal_id == self.id:
            raise DomainValidationError("disruption proposal cannot supersede itself")
        proposed_at = _utc(self.proposed_at, field_name="proposed_at")
        superseded_at = (
            _utc(self.superseded_at, field_name="superseded_at")
            if self.superseded_at is not None
            else None
        )
        if superseded_at is not None and superseded_at < proposed_at:
            raise DomainValidationError("proposal superseded_at cannot precede proposed_at")
        status = DisruptionProposalStatus(self.status)
        if status is DisruptionProposalStatus.CURRENT and superseded_at is not None:
            raise DomainValidationError("current disruption proposal cannot be superseded")
        if status is DisruptionProposalStatus.SUPERSEDED and superseded_at is None:
            raise DomainValidationError("superseded disruption proposal requires superseded_at")
        source = _required_text(self.source, field_name="proposal source", max_length=64)
        evidence = _text(
            self.source_evidence,
            field_name="proposal source_evidence",
            max_length=2000,
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_evidence", evidence)
        object.__setattr__(self, "proposed_at", proposed_at)
        object.__setattr__(self, "superseded_at", superseded_at)


@dataclass(frozen=True, slots=True)
class DisruptionCommercialChange:
    id: DisruptionCommercialChangeId
    disruption_id: DisruptionId
    proposal_id: DisruptionProposalId
    revision_number: int
    supersedes_change_id: DisruptionCommercialChangeId | None
    status: DisruptionCommercialChangeStatus
    original_quote_id: QuoteId
    currency: Currency
    normalization_version: str
    original_expected_total: Money
    original_worst_case_total: Money
    known_adjustment: Money
    conditional_adjustment: Money
    resulting_expected_total: Money
    resulting_worst_case_total: Money
    terms_summary: str | None
    created_at: datetime
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision_number, int) or isinstance(self.revision_number, bool):
            raise DomainValidationError("commercial revision_number must be an integer")
        if self.revision_number < 1:
            raise DomainValidationError("commercial revision_number must be positive")
        if self.revision_number == 1 and self.supersedes_change_id is not None:
            raise DomainValidationError("initial disruption commercial change cannot supersede one")
        if self.revision_number > 1 and self.supersedes_change_id is None:
            raise DomainValidationError("revised commercial change must identify its predecessor")
        if self.supersedes_change_id == self.id:
            raise DomainValidationError("commercial change cannot supersede itself")
        normalization_version = _required_text(
            self.normalization_version,
            field_name="normalization_version",
            max_length=64,
        )
        values = (
            self.original_expected_total,
            self.original_worst_case_total,
            self.known_adjustment,
            self.conditional_adjustment,
            self.resulting_expected_total,
            self.resulting_worst_case_total,
        )
        if any(value.currency != self.currency for value in values):
            raise DomainValidationError("all disruption commercial values must use one currency")
        expected = self.original_expected_total + self.known_adjustment
        worst_case = (
            self.original_worst_case_total + self.known_adjustment + self.conditional_adjustment
        )
        if self.resulting_expected_total != expected:
            raise DomainValidationError("resulting expected total is inconsistent with adjustment")
        if self.resulting_worst_case_total != worst_case:
            raise DomainValidationError(
                "resulting worst-case total is inconsistent with adjustment"
            )
        if self.resulting_expected_total.amount_minor < 0:
            raise DomainValidationError("resulting expected total cannot be negative")
        if self.resulting_worst_case_total.amount_minor < 0:
            raise DomainValidationError("resulting worst-case total cannot be negative")
        terms = _text(self.terms_summary, field_name="terms_summary", max_length=2000)
        if (
            self.known_adjustment.amount_minor == 0
            and self.conditional_adjustment.amount_minor == 0
            and terms is None
        ):
            raise DomainValidationError("commercial change cannot be a no-op")
        created_at = _utc(self.created_at, field_name="created_at")
        superseded_at = (
            _utc(self.superseded_at, field_name="superseded_at")
            if self.superseded_at is not None
            else None
        )
        if superseded_at is not None and superseded_at < created_at:
            raise DomainValidationError("commercial superseded_at cannot precede created_at")
        status = DisruptionCommercialChangeStatus(self.status)
        if status is DisruptionCommercialChangeStatus.CURRENT and superseded_at is not None:
            raise DomainValidationError("current commercial change cannot be superseded")
        if status is DisruptionCommercialChangeStatus.SUPERSEDED and superseded_at is None:
            raise DomainValidationError("superseded commercial change requires superseded_at")
        object.__setattr__(self, "normalization_version", normalization_version)
        object.__setattr__(self, "terms_summary", terms)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "superseded_at", superseded_at)
        object.__setattr__(self, "status", status)

    @property
    def evidence_key(self) -> str:
        return f"{self.proposal_id}:{self.id}"


@dataclass(frozen=True, slots=True)
class DisruptionBuyerDecision:
    id: DisruptionBuyerDecisionId
    disruption_id: DisruptionId
    proposal_id: DisruptionProposalId
    commercial_change_id: DisruptionCommercialChangeId | None
    buyer_id: OrganizationId
    decision: DisruptionBuyerDecisionValue
    decided_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", DisruptionBuyerDecisionValue(self.decision))
        object.__setattr__(self, "decided_at", _utc(self.decided_at, field_name="decided_at"))
        object.__setattr__(
            self,
            "note",
            _text(self.note, field_name="buyer decision note", max_length=1000),
        )

    @property
    def evidence_key(self) -> str:
        change = str(self.commercial_change_id) if self.commercial_change_id is not None else "none"
        return f"{self.proposal_id}:{change}"


class Disruption(AggregateRoot[DisruptionId]):
    aggregate_type = "disruption"

    def __init__(
        self,
        disruption_id: DisruptionId,
        *,
        booking_id: BookingId,
        disruption_type: DisruptionType,
        status: DisruptionStatus,
        detected_at: datetime,
        effective_at: datetime | None,
        reason: str,
        current_proposal_id: DisruptionProposalId | None = None,
        current_commercial_change_id: DisruptionCommercialChangeId | None = None,
        latest_buyer_decision_id: DisruptionBuyerDecisionId | None = None,
        selected_proposal_id: DisruptionProposalId | None = None,
        selected_commercial_change_id: DisruptionCommercialChangeId | None = None,
        selected_buyer_decision_id: DisruptionBuyerDecisionId | None = None,
        resolved_at: datetime | None = None,
        resolution_outcome: str | None = None,
        booking_state_at_resolution: BookingState | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(disruption_id, version=version)
        self.booking_id = booking_id
        self.disruption_type = DisruptionType(disruption_type)
        self.status = DisruptionStatus(status)
        self.detected_at = _utc(detected_at, field_name="detected_at")
        self.effective_at = (
            _utc(effective_at, field_name="effective_at") if effective_at is not None else None
        )
        self.reason = _required_text(reason, field_name="reason", max_length=2000)
        self.current_proposal_id = current_proposal_id
        self.current_commercial_change_id = current_commercial_change_id
        self.latest_buyer_decision_id = latest_buyer_decision_id
        self.selected_proposal_id = selected_proposal_id
        self.selected_commercial_change_id = selected_commercial_change_id
        self.selected_buyer_decision_id = selected_buyer_decision_id
        self.resolved_at = (
            _utc(resolved_at, field_name="resolved_at") if resolved_at is not None else None
        )
        self.resolution_outcome = _text(
            resolution_outcome,
            field_name="resolution_outcome",
            max_length=2000,
        )
        self.booking_state_at_resolution = (
            BookingState(booking_state_at_resolution)
            if booking_state_at_resolution is not None
            else None
        )
        self._validate_state()

    def _validate_state(self) -> None:
        if self.status is DisruptionStatus.OPEN and self.current_proposal_id is not None:
            raise DomainValidationError("open disruption cannot already have a current proposal")
        if self.status is DisruptionStatus.RESOLVED:
            if (
                self.selected_proposal_id is None
                or self.resolved_at is None
                or self.resolution_outcome is None
                or self.booking_state_at_resolution is None
            ):
                raise DomainValidationError(
                    "resolved disruption requires complete resolution evidence"
                )
        elif any(
            value is not None
            for value in (
                self.selected_proposal_id,
                self.selected_commercial_change_id,
                self.selected_buyer_decision_id,
                self.resolved_at,
                self.resolution_outcome,
                self.booking_state_at_resolution,
            )
        ):
            raise DomainValidationError("unresolved disruption cannot contain resolution evidence")

    @classmethod
    def open(
        cls,
        *,
        booking_id: BookingId,
        disruption_type: DisruptionType,
        detected_at: datetime,
        effective_at: datetime | None,
        reason: str,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> Disruption:
        disruption = cls(
            DisruptionId.new(),
            booking_id=booking_id,
            disruption_type=disruption_type,
            status=DisruptionStatus.OPEN,
            detected_at=detected_at,
            effective_at=effective_at,
            reason=reason,
        )
        disruption._record_event(
            "DISRUPTION_OPENED",
            {
                "booking_id": str(booking_id),
                "disruption_type": disruption.disruption_type.value,
                "detected_at": _iso(disruption.detected_at),
                "effective_at": (
                    _iso(disruption.effective_at) if disruption.effective_at is not None else None
                ),
                "reason": disruption.reason,
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=disruption.detected_at,
        )
        return disruption

    def record_proposal(
        self,
        proposal: ReplacementProposal,
        *,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if proposal.disruption_id != self.id:
            raise DomainValidationError("replacement proposal belongs to another disruption")
        self.current_proposal_id = proposal.id
        self.current_commercial_change_id = None
        self.latest_buyer_decision_id = None
        self.status = (
            DisruptionStatus.AWAITING_BUYER
            if proposal.requires_buyer_decision
            else DisruptionStatus.PROPOSED
        )
        self._record_event(
            "DISRUPTION_PROPOSAL_CREATED",
            {
                "proposal_id": str(proposal.id),
                "revision_number": proposal.revision_number,
                "supersedes_proposal_id": (
                    str(proposal.supersedes_proposal_id)
                    if proposal.supersedes_proposal_id is not None
                    else None
                ),
                "proposed_operator_id": str(proposal.proposed_operator_id),
                "proposed_aircraft_id": str(proposal.proposed_aircraft_id),
                "departure_window": (
                    {
                        "start": _iso(proposal.departure_window.start),
                        "end": _iso(proposal.departure_window.end),
                    }
                    if proposal.departure_window is not None
                    else None
                ),
                "requires_buyer_decision": proposal.requires_buyer_decision,
                "source": proposal.source,
                "source_evidence": proposal.source_evidence,
                "proposed_at": _iso(proposal.proposed_at),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=proposal.proposed_at,
        )

    def record_proposal_superseded(
        self,
        proposal_id: DisruptionProposalId,
        *,
        replacement_proposal_id: DisruptionProposalId,
        superseded_at: datetime,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        when = _utc(superseded_at, field_name="superseded_at")
        self._record_event(
            "DISRUPTION_PROPOSAL_SUPERSEDED",
            {
                "proposal_id": str(proposal_id),
                "replacement_proposal_id": str(replacement_proposal_id),
                "superseded_at": _iso(when),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_commercial_change(
        self,
        change: DisruptionCommercialChange,
        *,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if change.disruption_id != self.id:
            raise DomainValidationError("commercial change belongs to another disruption")
        if self.current_proposal_id != change.proposal_id:
            raise DomainValidationError("commercial change must target the current proposal")
        self.current_commercial_change_id = change.id
        self.latest_buyer_decision_id = None
        self.status = DisruptionStatus.AWAITING_BUYER
        self._record_event(
            "DISRUPTION_COMMERCIAL_CHANGE_CREATED",
            {
                "commercial_change_id": str(change.id),
                "proposal_id": str(change.proposal_id),
                "revision_number": change.revision_number,
                "supersedes_change_id": (
                    str(change.supersedes_change_id)
                    if change.supersedes_change_id is not None
                    else None
                ),
                "original_quote_id": str(change.original_quote_id),
                "currency": str(change.currency),
                "normalization_version": change.normalization_version,
                "original_expected_total_minor": change.original_expected_total.amount_minor,
                "original_worst_case_total_minor": change.original_worst_case_total.amount_minor,
                "known_adjustment_minor": change.known_adjustment.amount_minor,
                "conditional_adjustment_minor": change.conditional_adjustment.amount_minor,
                "resulting_expected_total_minor": change.resulting_expected_total.amount_minor,
                "resulting_worst_case_total_minor": change.resulting_worst_case_total.amount_minor,
                "terms_summary": change.terms_summary,
                "created_at": _iso(change.created_at),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=change.created_at,
        )

    def record_commercial_change_superseded(
        self,
        change_id: DisruptionCommercialChangeId,
        *,
        replacement_change_id: DisruptionCommercialChangeId,
        superseded_at: datetime,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        when = _utc(superseded_at, field_name="superseded_at")
        self._record_event(
            "DISRUPTION_COMMERCIAL_CHANGE_SUPERSEDED",
            {
                "commercial_change_id": str(change_id),
                "replacement_change_id": str(replacement_change_id),
                "superseded_at": _iso(when),
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def record_buyer_decision(
        self,
        decision: DisruptionBuyerDecision,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if decision.disruption_id != self.id:
            raise DomainValidationError("buyer decision belongs to another disruption")
        if decision.proposal_id != self.current_proposal_id:
            raise DomainValidationError("buyer decision must target the current proposal")
        if decision.commercial_change_id != self.current_commercial_change_id:
            raise DomainValidationError("buyer decision must target current commercial evidence")
        self.latest_buyer_decision_id = decision.id
        self.status = (
            DisruptionStatus.BUYER_APPROVED
            if decision.decision is DisruptionBuyerDecisionValue.APPROVED
            else DisruptionStatus.BUYER_REJECTED
        )
        self._record_event(
            (
                "DISRUPTION_BUYER_APPROVED"
                if decision.decision is DisruptionBuyerDecisionValue.APPROVED
                else "DISRUPTION_BUYER_REJECTED"
            ),
            {
                "buyer_decision_id": str(decision.id),
                "proposal_id": str(decision.proposal_id),
                "commercial_change_id": (
                    str(decision.commercial_change_id)
                    if decision.commercial_change_id is not None
                    else None
                ),
                "decision": decision.decision.value,
                "decided_at": _iso(decision.decided_at),
                "note": decision.note,
            },
            actor_id=decision.buyer_id,
            correlation_id=correlation_id,
            occurred_at=decision.decided_at,
        )

    def resolve(
        self,
        *,
        proposal: ReplacementProposal,
        commercial_change: DisruptionCommercialChange | None,
        buyer_decision: DisruptionBuyerDecision | None,
        booking_state: BookingState,
        resolved_at: datetime,
        outcome: str,
        actor_id: TypedId,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        self._require_mutable()
        if proposal.id != self.current_proposal_id or proposal.disruption_id != self.id:
            raise DomainValidationError("resolution must select the current disruption proposal")
        if commercial_change is None:
            if self.current_commercial_change_id is not None:
                raise DomainValidationError("resolution is missing current commercial evidence")
        elif (
            commercial_change.id != self.current_commercial_change_id
            or commercial_change.proposal_id != proposal.id
        ):
            raise DomainValidationError("resolution commercial evidence is stale")

        buyer_required = proposal.requires_buyer_decision or commercial_change is not None
        if buyer_required and (
            buyer_decision is None
            or buyer_decision.id != self.latest_buyer_decision_id
            or buyer_decision.proposal_id != proposal.id
            or buyer_decision.commercial_change_id != self.current_commercial_change_id
            or buyer_decision.decision is not DisruptionBuyerDecisionValue.APPROVED
            or self.status is not DisruptionStatus.BUYER_APPROVED
        ):
            raise DomainValidationError("current disruption evidence requires buyer approval")
        when = _utc(resolved_at, field_name="resolved_at")
        if when < proposal.proposed_at:
            raise DomainValidationError("resolved_at cannot precede the selected proposal")
        resolution = _required_text(
            outcome,
            field_name="resolution outcome",
            max_length=2000,
        )
        self.status = DisruptionStatus.RESOLVED
        self.selected_proposal_id = proposal.id
        self.selected_commercial_change_id = (
            commercial_change.id if commercial_change is not None else None
        )
        self.selected_buyer_decision_id = (
            buyer_decision.id if buyer_decision is not None else None
        )
        self.resolved_at = when
        self.resolution_outcome = resolution
        self.booking_state_at_resolution = BookingState(booking_state)
        self._record_event(
            "DISRUPTION_RESOLVED",
            {
                "selected_proposal_id": str(proposal.id),
                "selected_commercial_change_id": (
                    str(commercial_change.id) if commercial_change is not None else None
                ),
                "selected_buyer_decision_id": (
                    str(buyer_decision.id) if buyer_decision is not None else None
                ),
                "resolved_at": _iso(when),
                "resolution_outcome": resolution,
                "booking_state_at_resolution": self.booking_state_at_resolution.value,
            },
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )

    def _require_mutable(self) -> None:
        if self.status is DisruptionStatus.RESOLVED:
            raise DomainValidationError("resolved disruption is terminal")
