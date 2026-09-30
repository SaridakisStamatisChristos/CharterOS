from __future__ import annotations

from datetime import UTC, datetime

from charteros.application.capacity import AircraftCapacityPolicy
from charteros.application.evidence import DecisionEvidenceWriter
from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.feasibility import AircraftMissionFeasibilityService
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.capacity import AircraftCapacityReservationRepository
from charteros.application.ports.catalog import DomainEventRepository
from charteros.application.ports.contracts import ContractRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.application.ports.tenders import TenderRepository
from charteros.domain.bookings import (
    Booking,
    BookingId,
    BookingState,
    BookingTerminationReason,
)
from charteros.domain.capacity_reservations import (
    AircraftCapacityReservation,
    AircraftCapacityReservationStatus,
)
from charteros.domain.contracts import ContractStatus
from charteros.domain.missions import Mission, MissionStatus
from charteros.domain.quotes import QuoteId, QuoteStatus
from charteros.domain.rfqs import RfqStatus
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.tenders import TenderId, TenderStatus

AWARD_REVALIDATION_POLICY_VERSION = "award-truth-gate-v1"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class BookingService:
    def __init__(
        self,
        *,
        bookings: BookingRepository,
        capacity_policy: AircraftCapacityPolicy,
        capacity_reservations: AircraftCapacityReservationRepository,
        feasibility: AircraftMissionFeasibilityService,
        decision_evidence: DecisionEvidenceWriter,
        quotes: QuoteRepository,
        rfqs: RfqRepository,
        missions: MissionRepository,
        events: DomainEventRepository,
        contracts: ContractRepository | None = None,
        tenders: TenderRepository | None = None,
    ) -> None:
        self._bookings = bookings
        self._capacity_policy = capacity_policy
        self._capacity_reservations = capacity_reservations
        self._feasibility = feasibility
        self._decision_evidence = decision_evidence
        self._quotes = quotes
        self._rfqs = rfqs
        self._missions = missions
        self._events = events
        self._contracts = contracts
        self._tenders = tenders

    def accept_quote(
        self,
        *,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
        tender_id: TenderId | None = None,
    ) -> Booking:
        accepted_at = _utc(now, field_name="now")

        observed = self._quotes.get(quote_id)
        if observed is None:
            raise EntityNotFoundError("quote does not exist")
        observed_rfq = self._rfqs.get(observed.rfq_id)
        if observed_rfq is None:
            raise EntityNotFoundError("quote RFQ does not exist")

        if self._tenders is not None:
            invitation = self._tenders.find_invitation_for_rfq(observed.rfq_id)
            if invitation is not None:
                tender = self._tenders.get_for_update(invitation.tender_id)
                if tender is None:
                    raise EntityNotFoundError("tender for quote invitation does not exist")
                if tender_id is None or tender.id != tender_id:
                    raise EntityConflictError(
                        "tender quotes must be awarded through the tender workflow"
                    )
                if tender.status is not TenderStatus.CLOSED:
                    raise EntityConflictError(
                        "tender must be closed before its quote can be awarded"
                    )
            elif tender_id is not None:
                raise EntityConflictError("award quote does not belong to the requested tender")

        # The mission row is the serialization point for the award boundary. Quote submission and
        # revision take the same lock, so no fresh current quote can appear after this snapshot.
        mission = self._missions.get_for_update(observed_rfq.mission_id)
        if mission is None:
            raise EntityNotFoundError("quote mission does not exist")
        if mission.status not in (MissionStatus.SOURCING, MissionStatus.QUOTED):
            raise EntityConflictError("mission has already been awarded or is not awardable")
        if self._bookings.get_for_mission(mission.id) is not None:
            raise EntityConflictError("mission already has a booking")

        rfqs = self._rfqs.list_for_mission(mission.id)
        rfq_by_id = {rfq.id: rfq for rfq in rfqs}
        current_quotes = self._quotes.list_current_for_rfqs_for_update(
            tuple(rfq.id for rfq in rfqs)
        )
        target = next((quote for quote in current_quotes if quote.id == quote_id), None)
        if target is None:
            raise EntityConflictError("only the current submitted quote can be accepted")

        target_rfq = rfq_by_id.get(target.rfq_id)
        if target_rfq is None or target_rfq.status is not RfqStatus.QUOTED:
            raise EntityConflictError("accepted quote must belong to a quoted RFQ")
        if target.status is not QuoteStatus.SUBMITTED or not target.is_current:
            raise EntityConflictError("only the current submitted quote can be accepted")
        if accepted_at >= target.valid_until:
            raise EntityConflictError("expired quote cannot be accepted")

        feasibility = self._feasibility.evaluate(
            mission=mission,
            aircraft_id=target.aircraft_id,
            known_as_of=accepted_at,
            lock_catalog=True,
        )
        if feasibility.candidate.operator_id != target_rfq.operator_id:
            raise EntityConflictError(
                "quoted aircraft no longer belongs to the quoted operator"
            )
        if not feasibility.feasible:
            reasons = ",".join(
                reason.value for reason in feasibility.evaluation.rejection_reasons
            )
            raise EntityConflictError(
                f"quoted aircraft is no longer feasible for award ({reasons})"
            )
        draft = feasibility.evaluation.draft
        if draft is None:
            raise RuntimeError("feasible award candidate is missing its canonical matching draft")

        capacity_plan = self._capacity_policy.derive(
            mission=mission,
            aircraft_id=target.aircraft_id,
            operator_id=target_rfq.operator_id,
            known_as_of=accepted_at,
        )
        if (
            capacity_plan.reference_profile_id != draft.reference_profile.id.value
            or capacity_plan.reference_profile_recorded_at
            != draft.reference_profile.recorded_at
            or capacity_plan.route_distance_tenths_nm != draft.route_distance_tenths_nm
            or capacity_plan.route_minutes != draft.route_minutes
            or capacity_plan.turnaround_buffer_minutes
            != draft.reference_profile.turnaround_buffer_minutes
        ):
            raise EntityConflictError(
                "award feasibility and capacity evidence are inconsistent"
            )
        if self._capacity_reservations.has_reserved_overlap(
            aircraft_id=target.aircraft_id,
            interval=capacity_plan.interval,
        ):
            raise EntityConflictError(
                "aircraft is already committed to overlapping charter capacity"
            )
        booking = Booking.create(
            mission_id=mission.id,
            accepted_quote_id=target.id,
            operator_id=target_rfq.operator_id,
            aircraft_id=target.aircraft_id,
            created_at=accepted_at,
            correlation_id=correlation_id,
        )
        reservation = AircraftCapacityReservation.create(
            aircraft_id=target.aircraft_id,
            booking_id=booking.id,
            mission_id=mission.id,
            operator_id=target_rfq.operator_id,
            interval=capacity_plan.interval,
            created_at=accepted_at,
            policy_version=capacity_plan.policy_version,
            reference_profile_id=capacity_plan.reference_profile_id,
            reference_profile_recorded_at=capacity_plan.reference_profile_recorded_at,
            route_distance_tenths_nm=capacity_plan.route_distance_tenths_nm,
            route_minutes=capacity_plan.route_minutes,
            turnaround_buffer_minutes=capacity_plan.turnaround_buffer_minutes,
            correlation_id=correlation_id,
        )

        # Flush the Booking first to satisfy the reservation FK, then reserve capacity before any
        # Quote/Mission state mutation. A PostgreSQL exclusion conflict aborts the transaction.
        expected_mission_version = mission.version
        expected_quote_versions = {quote.id: quote.version for quote in current_quotes}

        self._bookings.add(booking)
        self._capacity_reservations.add(reservation)
        self._decision_evidence.add_snapshot(
            decision_type="award_commit",
            subject_type="mission",
            subject_id=mission.id.value,
            source_aggregate_type="booking",
            source_aggregate_id=booking.id.value,
            decided_at=accepted_at,
            known_as_of=accepted_at,
            actor_id=None,
            correlation_id=correlation_id.value,
            policy_versions={
                "award_revalidation": AWARD_REVALIDATION_POLICY_VERSION,
                "matching": feasibility.policy_version,
                "capacity": capacity_plan.policy_version,
            },
            content={
                "feasible": True,
                "mission_id": str(mission.id),
                "mission_version": expected_mission_version,
                "quote_id": str(target.id),
                "quote_version": expected_quote_versions[target.id],
                "quote_revision_number": target.revision_number,
                "rfq_id": str(target_rfq.id),
                "rfq_version": target_rfq.version,
                "operator_id": str(target_rfq.operator_id),
                "operator_version": feasibility.candidate.operator_version,
                "aircraft_id": str(target.aircraft_id),
                "aircraft_version": feasibility.candidate.aircraft_version,
                "decision_timestamp": accepted_at,
                "position": {
                    "id": str(draft.position.id),
                    "event_time": draft.position.event_time,
                    "recorded_at": draft.position.recorded_at,
                },
                "availability": {
                    "id": str(draft.availability.id),
                    "status": draft.availability.status.value,
                    "valid_from": draft.availability.interval.start,
                    "valid_to": draft.availability.interval.end,
                    "recorded_at": draft.availability.recorded_at,
                },
                "reference_profile": {
                    "id": str(draft.reference_profile.id),
                    "recorded_at": draft.reference_profile.recorded_at,
                },
                "route_distance_tenths_nm": draft.route_distance_tenths_nm,
                "required_range_nm": draft.required_range_nm,
                "reposition_distance_tenths_nm": draft.reposition_distance_tenths_nm,
                "route_minutes": draft.route_minutes,
                "reposition_minutes": draft.reposition_minutes,
                "timing_buffer_minutes": draft.timing_buffer_minutes,
                "reason_codes": [reason.value for reason in draft.reason_codes],
                "capacity_interval": {
                    "start": capacity_plan.interval.start,
                    "end": capacity_plan.interval.end,
                },
            },
        )

        target.accept(
            accepted_at=accepted_at,
            booking_id=str(booking.id),
            correlation_id=correlation_id,
        )
        for quote in current_quotes:
            if quote.id == target.id:
                continue
            quote.reject(
                rejected_at=accepted_at,
                accepted_quote_id=str(target.id),
                correlation_id=correlation_id,
            )

        mission.select_quote(
            quote_id=str(target.id),
            booking_id=str(booking.id),
            selected_at=accepted_at,
            correlation_id=correlation_id,
        )

        for quote in current_quotes:
            self._quotes.save(quote, expected_version=expected_quote_versions[quote.id])
        self._missions.save(mission, expected_version=expected_mission_version)

        for quote in current_quotes:
            self._events.add_aggregate_events(quote)
        self._events.add_aggregate_events(mission)
        self._events.add_aggregate_events(booking)
        self._events.add_aggregate_events(reservation)
        return booking

    def mark_contracted(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.PENDING_CONTRACT)

        if self._contracts is None:
            raise RuntimeError("contract repository is required for booking workflow transitions")
        contract = self._contracts.get_for_booking_for_update(booking.id)
        if contract is None:
            raise EntityConflictError("booking requires an accepted contract before contracting")
        if (
            contract.booking_id != booking.id
            or contract.status is not ContractStatus.ACCEPTED
            or contract.buyer_signed_at is None
            or contract.operator_signed_at is None
            or contract.accepted_at is None
        ):
            raise EntityConflictError("booking requires an accepted contract before contracting")

        accepted_at = contract.accepted_at
        if accepted_at is None:
            raise EntityConflictError("booking requires an accepted contract before contracting")
        transitioned_at = max(requested_at, booking.state_changed_at, accepted_at)
        mission = self._lock_mission(booking, expected_status=MissionStatus.SELECTED)
        expected_booking_version = booking.version
        expected_mission_version = mission.version
        booking.mark_contracted(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        mission.begin_contracting(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        self._persist(
            booking=booking,
            expected_booking_version=expected_booking_version,
            mission=mission,
            expected_mission_version=expected_mission_version,
        )
        return booking

    def mark_payment_pending(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.CONTRACTED)
        transitioned_at = max(requested_at, booking.state_changed_at)
        self._lock_mission(booking, expected_status=MissionStatus.CONTRACTING)
        expected_booking_version = booking.version
        booking.mark_payment_pending(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        self._bookings.save(booking, expected_version=expected_booking_version)
        self._events.add_aggregate_events(booking)
        return booking

    def confirm(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.PAYMENT_PENDING)
        transitioned_at = max(requested_at, booking.state_changed_at)
        mission = self._lock_mission(booking, expected_status=MissionStatus.CONTRACTING)
        expected_booking_version = booking.version
        expected_mission_version = mission.version
        booking.confirm(transitioned_at=transitioned_at, correlation_id=correlation_id)
        mission.mark_booked(transitioned_at=transitioned_at, correlation_id=correlation_id)
        self._persist(
            booking=booking,
            expected_booking_version=expected_booking_version,
            mission=mission,
            expected_mission_version=expected_mission_version,
        )
        return booking

    def enter_pre_operation(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.CONFIRMED)
        transitioned_at = max(requested_at, booking.state_changed_at)
        self._lock_mission(booking, expected_status=MissionStatus.BOOKED)
        expected_booking_version = booking.version
        booking.enter_pre_operation(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        self._bookings.save(booking, expected_version=expected_booking_version)
        self._events.add_aggregate_events(booking)
        return booking

    def start_operation(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.PRE_OPERATION)
        transitioned_at = max(requested_at, booking.state_changed_at)
        mission = self._lock_mission(booking, expected_status=MissionStatus.BOOKED)
        expected_booking_version = booking.version
        expected_mission_version = mission.version
        booking.start_operation(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        mission.start_operating(
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        self._persist(
            booking=booking,
            expected_booking_version=expected_booking_version,
            mission=mission,
            expected_mission_version=expected_mission_version,
        )
        return booking

    def complete(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.OPERATING)
        transitioned_at = max(requested_at, booking.state_changed_at)
        mission = self._lock_mission(booking, expected_status=MissionStatus.OPERATING)
        expected_booking_version = booking.version
        expected_mission_version = mission.version
        booking.complete(transitioned_at=transitioned_at, correlation_id=correlation_id)
        mission.complete(transitioned_at=transitioned_at, correlation_id=correlation_id)
        self._persist(
            booking=booking,
            expected_booking_version=expected_booking_version,
            mission=mission,
            expected_mission_version=expected_mission_version,
        )
        return booking

    def reconcile(
        self,
        *,
        booking_id: BookingId,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._lock_booking(booking_id, expected_state=BookingState.COMPLETED)
        transitioned_at = max(requested_at, booking.state_changed_at)
        self._lock_mission(booking, expected_status=MissionStatus.COMPLETED)
        expected_booking_version = booking.version
        booking.reconcile(transitioned_at=transitioned_at, correlation_id=correlation_id)
        self._bookings.save(booking, expected_version=expected_booking_version)
        self._events.add_aggregate_events(booking)
        return booking

    def terminate(
        self,
        *,
        booking_id: BookingId,
        reason: BookingTerminationReason,
        now: datetime,
        correlation_id: CorrelationId,
    ) -> Booking:
        requested_at = _utc(now, field_name="now")
        booking = self._bookings.get_for_update(booking_id)
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        normalized_reason = BookingTerminationReason(reason)
        self._validate_termination_reason(booking, normalized_reason)
        if normalized_reason is BookingTerminationReason.CONTRACT_UNSIGNED:
            if self._contracts is None:
                raise RuntimeError("contract repository is required for contract expiry")
            contract = self._contracts.get_for_booking_for_update(booking.id)
            if contract is not None and contract.status is ContractStatus.ACCEPTED:
                raise EntityConflictError("accepted contract cannot expire as contract_unsigned")

        expected_mission_status = (
            MissionStatus.SELECTED
            if booking.state is BookingState.PENDING_CONTRACT
            else MissionStatus.CONTRACTING
        )
        mission = self._lock_mission(booking, expected_status=expected_mission_status)
        reservation = self._capacity_reservations.get_for_booking_for_update(booking.id)
        if reservation is None:
            raise EntityConflictError("booking has no aircraft capacity reservation")
        if (
            reservation.booking_id != booking.id
            or reservation.mission_id != booking.mission_id
            or reservation.aircraft_id != booking.aircraft_id
            or reservation.operator_id != booking.operator_id
        ):
            raise EntityConflictError(
                "booking capacity reservation identity does not match booking"
            )
        if reservation.status is not AircraftCapacityReservationStatus.RESERVED:
            raise EntityConflictError("booking aircraft capacity is already released")

        transitioned_at = max(requested_at, booking.state_changed_at, reservation.created_at)
        expected_booking_version = booking.version
        expected_mission_version = mission.version
        expected_reservation_version = reservation.version

        booking.terminate(
            reason=normalized_reason,
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        if booking.termination_source is None:
            raise RuntimeError("terminal booking must record a termination source")
        expired = booking.state is BookingState.EXPIRED
        mission.terminate_booking(
            expired=expired,
            reason=normalized_reason.value,
            source=booking.termination_source.value,
            transitioned_at=transitioned_at,
            correlation_id=correlation_id,
        )
        reservation.release(
            released_at=transitioned_at,
            reason=normalized_reason.value,
            correlation_id=correlation_id,
        )

        self._bookings.save(booking, expected_version=expected_booking_version)
        self._missions.save(mission, expected_version=expected_mission_version)
        self._capacity_reservations.save(
            reservation,
            expected_version=expected_reservation_version,
        )
        self._events.add_aggregate_events(booking)
        self._events.add_aggregate_events(mission)
        self._events.add_aggregate_events(reservation)
        return booking

    def get_booking(self, booking_id: BookingId) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        return booking

    @staticmethod
    def _validate_termination_reason(
        booking: Booking,
        reason: BookingTerminationReason,
    ) -> None:
        if booking.state not in (
            BookingState.PENDING_CONTRACT,
            BookingState.CONTRACTED,
            BookingState.PAYMENT_PENDING,
        ):
            raise EntityConflictError(
                "only pending_contract, contracted, or payment_pending bookings can terminate"
            )
        if reason is BookingTerminationReason.CONTRACT_UNSIGNED:
            if booking.state is not BookingState.PENDING_CONTRACT:
                raise EntityConflictError("contract_unsigned requires pending_contract")
        elif reason is BookingTerminationReason.DEPOSIT_TIMEOUT:
            if booking.state is not BookingState.PAYMENT_PENDING:
                raise EntityConflictError("deposit_timeout requires payment_pending")
        elif reason is BookingTerminationReason.COMMERCIAL_EXPIRY and booking.state not in (
            BookingState.PENDING_CONTRACT,
            BookingState.PAYMENT_PENDING,
        ):
            raise EntityConflictError(
                "commercial_expiry requires pending_contract or payment_pending"
            )

    def _lock_booking(self, booking_id: BookingId, *, expected_state: BookingState) -> Booking:
        booking = self._bookings.get_for_update(booking_id)
        if booking is None:
            raise EntityNotFoundError("booking does not exist")
        if booking.state is not expected_state:
            raise EntityConflictError(
                f"booking must be {expected_state.value} for this workflow transition"
            )
        return booking

    def _lock_mission(self, booking: Booking, *, expected_status: MissionStatus) -> Mission:
        mission = self._missions.get_for_update(booking.mission_id)
        if mission is None:
            raise EntityNotFoundError("booking mission does not exist")
        if mission.status is not expected_status:
            raise EntityConflictError(
                f"booking mission must be {expected_status.value} for this workflow transition"
            )
        return mission

    def _persist(
        self,
        *,
        booking: Booking,
        expected_booking_version: int,
        mission: Mission,
        expected_mission_version: int,
    ) -> None:
        self._bookings.save(booking, expected_version=expected_booking_version)
        self._missions.save(mission, expected_version=expected_mission_version)
        self._events.add_aggregate_events(booking)
        self._events.add_aggregate_events(mission)
