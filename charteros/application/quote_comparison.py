from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.catalog import AirportRepository
from charteros.application.ports.matching import MatchingSnapshotRepository
from charteros.application.ports.missions import MissionRepository
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.rfqs import RfqRepository
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.quotes import Quote, QuoteId, QuoteStatus
from charteros.domain.quotes.comparison import (
    COMPARISON_POLICY_VERSION,
    ComparisonDraft,
    ComparisonEligibilityReason,
    ComparisonScoreDecomposition,
    score_comparison_drafts,
)
from charteros.domain.quotes.normalization import QuoteNormalization, normalize_quote
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.matching import POLICY_VERSION as MATCHING_POLICY_VERSION
from charteros.matching import (
    MatchReasonCode,
    evaluate_candidate,
    haversine_distance_tenths_nm,
    required_range_nm,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("evaluated_at must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AircraftSuitabilityComparison:
    feasible: bool
    reason_codes: tuple[MatchReasonCode, ...]
    rejection_reasons: tuple[MatchReasonCode, ...]
    seat_capacity: int
    aircraft_range_nm: int
    required_range_nm: int
    reposition_distance_tenths_nm: int | None
    timing_buffer_minutes: int | None
    schedule_risk_basis_points: int | None
    position_event_time: datetime | None
    position_recorded_at: datetime | None
    availability_recorded_at: datetime | None
    reference_profile_recorded_at: datetime | None


@dataclass(frozen=True, slots=True)
class QuoteComparisonEntry:
    quote: Quote
    operator_id: OperatorId
    normalization: QuoteNormalization
    commercial_valid: bool
    decision_eligible: bool
    eligibility_reasons: tuple[ComparisonEligibilityReason, ...]
    aircraft_suitability: AircraftSuitabilityComparison
    score: ComparisonScoreDecomposition
    currency_rank: int | None


@dataclass(frozen=True, slots=True)
class MissionQuoteComparison:
    comparison_policy_version: str
    matching_policy_version: str
    mission_id: MissionId
    evaluated_at: datetime
    pricing_currencies: tuple[Currency, ...]
    global_rank_available: bool
    entries: tuple[QuoteComparisonEntry, ...]


class QuoteComparisonService:
    def __init__(
        self,
        *,
        missions: MissionRepository,
        rfqs: RfqRepository,
        quotes: QuoteRepository,
        airports: AirportRepository,
        snapshots: MatchingSnapshotRepository,
    ) -> None:
        self._missions = missions
        self._rfqs = rfqs
        self._quotes = quotes
        self._airports = airports
        self._snapshots = snapshots

    def compare(
        self,
        *,
        mission_id: MissionId,
        evaluated_at: datetime,
    ) -> MissionQuoteComparison:
        cutoff = _utc(evaluated_at)
        mission = self._missions.get(mission_id)
        if mission is None:
            raise EntityNotFoundError("mission does not exist")

        origin = self._airports.get(mission.origin_airport_id)
        destination = self._airports.get(mission.destination_airport_id)
        if origin is None or destination is None:
            raise EntityNotFoundError("mission airport dependency does not exist")

        rfqs = self._rfqs.list_for_mission(mission_id)
        if not rfqs:
            return MissionQuoteComparison(
                comparison_policy_version=COMPARISON_POLICY_VERSION,
                matching_policy_version=MATCHING_POLICY_VERSION,
                mission_id=mission_id,
                evaluated_at=cutoff,
                pricing_currencies=(),
                global_rank_available=True,
                entries=(),
            )

        rfq_by_id = {rfq.id: rfq for rfq in rfqs}
        quotes = self._quotes.list_current_for_rfqs(tuple(rfq_by_id))
        if not quotes:
            return MissionQuoteComparison(
                comparison_policy_version=COMPARISON_POLICY_VERSION,
                matching_policy_version=MATCHING_POLICY_VERSION,
                mission_id=mission_id,
                evaluated_at=cutoff,
                pricing_currencies=(),
                global_rank_available=True,
                entries=(),
            )

        aircraft_ids = tuple(dict.fromkeys(quote.aircraft_id for quote in quotes))
        position_event_cutoff = min(cutoff, mission.departure_window.start)
        candidates = self._snapshots.load_candidates_by_aircraft_ids(
            aircraft_ids=aircraft_ids,
            known_as_of=cutoff,
            position_event_cutoff=position_event_cutoff,
            availability_from=mission.departure_window.start,
            availability_to=mission.departure_window.end,
        )
        candidate_by_id = {candidate.aircraft_id: candidate for candidate in candidates}
        missing = [
            aircraft_id
            for aircraft_id in aircraft_ids
            if aircraft_id not in candidate_by_id
        ]
        if missing:
            raise EntityConflictError(
                "quoted aircraft is missing from the canonical operational snapshot"
            )

        route_distance = haversine_distance_tenths_nm(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        required_range = required_range_nm(route_distance)

        drafts: list[ComparisonDraft] = []
        prepared: dict[QuoteId, tuple[
            Quote,
            OperatorId,
            QuoteNormalization,
            bool,
            tuple[ComparisonEligibilityReason, ...],
            AircraftSuitabilityComparison,
        ]] = {}

        for quote in quotes:
            rfq = rfq_by_id.get(quote.rfq_id)
            if rfq is None:
                raise EntityConflictError("quote RFQ does not belong to requested mission")
            candidate = candidate_by_id[quote.aircraft_id]
            evaluation = evaluate_candidate(
                mission=mission,
                candidate=candidate,
                origin_latitude=origin.latitude,
                origin_longitude=origin.longitude,
                route_distance_tenths_nm=route_distance,
                decision_time=position_event_cutoff,
            )
            normalization = normalize_quote(quote)
            commercial_valid = (
                quote.status is QuoteStatus.SUBMITTED
                and quote.is_current
                and quote.submitted_at <= cutoff
                and cutoff < quote.valid_until
            )
            operationally_feasible = evaluation.draft is not None

            eligibility: list[ComparisonEligibilityReason] = []
            if not commercial_valid:
                eligibility.append(
                    ComparisonEligibilityReason.QUOTE_NOT_COMMERCIALLY_VALID
                )
            if not operationally_feasible:
                eligibility.append(ComparisonEligibilityReason.AIRCRAFT_INFEASIBLE)

            reposition_distance: int | None = None
            position_event_time: datetime | None = None
            position_recorded_at: datetime | None = None
            availability_recorded_at: datetime | None = None
            profile_recorded_at: datetime | None = None
            if candidate.position is not None:
                reposition_distance = haversine_distance_tenths_nm(
                    candidate.position.latitude,
                    candidate.position.longitude,
                    origin.latitude,
                    origin.longitude,
                )
                position_event_time = candidate.position.event_time
                position_recorded_at = candidate.position.recorded_at
            if candidate.availability is not None:
                availability_recorded_at = candidate.availability.recorded_at
            if candidate.reference_profile is not None:
                profile_recorded_at = candidate.reference_profile.recorded_at

            if evaluation.draft is not None:
                reason_codes = evaluation.draft.reason_codes
                rejection_reasons: tuple[MatchReasonCode, ...] = ()
                timing_buffer = evaluation.draft.timing_buffer_minutes
                schedule_risk = evaluation.draft.schedule_risk_basis_points
                reposition_distance = evaluation.draft.reposition_distance_tenths_nm
            else:
                reason_codes = ()
                rejection_reasons = evaluation.rejection_reasons
                timing_buffer = None
                schedule_risk = None

            suitability = AircraftSuitabilityComparison(
                feasible=operationally_feasible,
                reason_codes=reason_codes,
                rejection_reasons=rejection_reasons,
                seat_capacity=candidate.seat_capacity,
                aircraft_range_nm=candidate.range_nm,
                required_range_nm=required_range,
                reposition_distance_tenths_nm=reposition_distance,
                timing_buffer_minutes=timing_buffer,
                schedule_risk_basis_points=schedule_risk,
                position_event_time=position_event_time,
                position_recorded_at=position_recorded_at,
                availability_recorded_at=availability_recorded_at,
                reference_profile_recorded_at=profile_recorded_at,
            )
            decision_eligible = commercial_valid and operationally_feasible
            drafts.append(
                ComparisonDraft(
                    quote_id=quote.id,
                    expected_total=normalization.expected_total,
                    worst_case_total=normalization.worst_case_total,
                    reposition_distance_tenths_nm=(
                        suitability.reposition_distance_tenths_nm
                        if decision_eligible
                        else None
                    ),
                    schedule_risk_basis_points=(
                        suitability.schedule_risk_basis_points
                        if decision_eligible
                        else None
                    ),
                    pricing_confidence=normalization.confidence,
                    eligible=decision_eligible,
                )
            )
            prepared[quote.id] = (
                quote,
                rfq.operator_id,
                normalization,
                commercial_valid,
                tuple(eligibility),
                suitability,
            )

        scored = score_comparison_drafts(tuple(drafts))
        entries: list[QuoteComparisonEntry] = []
        for scored_item in scored:
            (
                quote,
                operator_id,
                normalization,
                commercial_valid,
                eligibility,
                suitability,
            ) = prepared[scored_item.quote_id]
            entries.append(
                QuoteComparisonEntry(
                    quote=quote,
                    operator_id=operator_id,
                    normalization=normalization,
                    commercial_valid=commercial_valid,
                    decision_eligible=not eligibility,
                    eligibility_reasons=eligibility,
                    aircraft_suitability=suitability,
                    score=scored_item.score,
                    currency_rank=scored_item.currency_rank,
                )
            )

        currencies = tuple(sorted({entry.normalization.currency for entry in entries}))
        return MissionQuoteComparison(
            comparison_policy_version=COMPARISON_POLICY_VERSION,
            matching_policy_version=MATCHING_POLICY_VERSION,
            mission_id=mission_id,
            evaluated_at=cutoff,
            pricing_currencies=currencies,
            global_rank_available=len(currencies) <= 1,
            entries=tuple(entries),
        )
