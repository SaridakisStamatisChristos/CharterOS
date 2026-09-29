from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from charteros.application.exceptions import EntityConflictError
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.application.quote_comparison import MissionQuoteComparison
from charteros.matching import MatchingDecision

PACKAGE_SCHEMA_VERSION = "audit-evidence-v1"
SNAPSHOT_SCHEMA_VERSION = "decision-evidence-v1"
SUPPLIER_SELECTION_POLICY_VERSION = "buyer-supplier-selection-v1"

type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]


class EvidenceSubjectType(StrEnum):
    MISSION = "mission"
    BOOKING = "booking"
    DISRUPTION = "disruption"
    RECONCILIATION = "reconciliation"


class EvidenceCompleteness(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"


@dataclass(frozen=True, slots=True)
class EvidenceParty:
    buyer_id: UUID | None = None
    operator_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.buyer_id is None) == (self.operator_id is None):
            raise DomainValidationError("exactly one buyer or operator evidence context is required")


@dataclass(frozen=True, slots=True)
class EvidenceSourceRecord:
    source_type: str
    source_id: UUID
    version: int | None
    facts: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class EvidenceEventRecord:
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    canonical_json: str


@dataclass(frozen=True, slots=True)
class DecisionEvidenceRecord:
    snapshot_id: UUID
    decision_type: str
    subject_type: str
    subject_id: UUID
    source_aggregate_type: str
    source_aggregate_id: UUID
    schema_version: str
    decided_at: datetime
    known_as_of: datetime | None
    actor_id: UUID | None
    correlation_id: UUID | None
    policy_versions: Mapping[str, object]
    content: Mapping[str, object]
    integrity_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceMaterial:
    sources: tuple[EvidenceSourceRecord, ...]
    events: tuple[EvidenceEventRecord, ...]
    decisions: tuple[DecisionEvidenceRecord, ...]
    truncated: bool = False
    diagnostics: tuple[str, ...] = ()


class EvidenceRepository(Protocol):
    def load(
        self,
        *,
        subject_type: EvidenceSubjectType,
        subject_id: UUID,
        party: EvidenceParty,
        event_limit: int,
    ) -> EvidenceMaterial: ...


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    schema_version: str
    subject_type: EvidenceSubjectType
    subject_id: UUID
    canonical_cutoff: datetime | None
    generated_at: datetime
    sources: tuple[dict[str, JsonValue], ...]
    events: tuple[dict[str, JsonValue], ...]
    decisions: tuple[dict[str, JsonValue], ...]
    policy_versions: tuple[str, ...]
    completeness: EvidenceCompleteness
    diagnostics: tuple[str, ...]
    integrity_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "subject_type": self.subject_type.value,
            "subject_id": str(self.subject_id),
            "canonical_cutoff": (
                _format_datetime(self.canonical_cutoff)
                if self.canonical_cutoff is not None
                else None
            ),
            "generated_at": _format_datetime(self.generated_at),
            "sources": list(self.sources),
            "events": list(self.events),
            "decisions": list(self.decisions),
            "policy_versions": list(self.policy_versions),
            "completeness": self.completeness.value,
            "diagnostics": list(self.diagnostics),
            "integrity_digest": self.integrity_digest,
        }


_SAFE_EVENT_PAYLOAD_KEYS = frozenset(
    {
        "accepted_at",
        "accepted_quote_id",
        "aircraft_id",
        "approved_at",
        "approved_variance_minor",
        "availability_record_id",
        "availability_recorded_at",
        "booking_id",
        "booking_state_at_resolution",
        "booked_amount_minor",
        "booked_worst_case_amount_minor",
        "buyer_decision_id",
        "buyer_id",
        "buyer_signed_at",
        "commercial_change_id",
        "commercial_change_ids",
        "completed_at",
        "consumed_at",
        "currency",
        "decided_at",
        "decision",
        "detected_at",
        "dispute_id",
        "disputed_amount_minor",
        "document_reference",
        "document_version",
        "effective_at",
        "from_state",
        "invoice_reference",
        "invoice_revision_id",
        "invoice_total_minor",
        "invoice_variance_minor",
        "known_adjustment_minor",
        "line_items",
        "mission_id",
        "note",
        "opened_at",
        "operator_id",
        "operator_signed_at",
        "original_expected_total_minor",
        "original_quote_id",
        "original_worst_case_total_minor",
        "proposal_id",
        "proposed_aircraft_id",
        "proposed_aircraft_version",
        "proposed_operator_id",
        "proposed_operator_version",
        "quote_id",
        "quote_normalization_version",
        "quote_revision_number",
        "reason",
        "replacement_change_id",
        "replacement_proposal_id",
        "replacement_quote_id",
        "resulting_expected_total_minor",
        "resulting_worst_case_total_minor",
        "resolution_outcome",
        "resolved_at",
        "resolves_dispute_id",
        "revision_number",
        "selected_buyer_decision_id",
        "selected_commercial_change_id",
        "selected_proposal_id",
        "state",
        "submitted_at",
        "superseded_at",
        "supersedes_approval_id",
        "supersedes_change_id",
        "supersedes_invoice_revision_id",
        "supersedes_proposal_id",
        "surcharge_reason",
        "terms_summary",
        "to_state",
        "transitioned_at",
        "variance_approval_id",
    }
)

_VERSIONED_SOURCE_TYPES = frozenset(
    {
        "mission",
        "rfq",
        "quote",
        "procurement_approval",
        "booking",
        "contract",
        "tender",
        "disruption",
        "financial_reconciliation",
    }
)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EntityConflictError("evidence contains a naive timestamp")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EntityConflictError("evidence contains a non-finite float")
        return format(value, ".17g")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EntityConflictError("evidence JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise EntityConflictError(f"unsupported evidence value type: {type(value).__name__}")


def canonical_json(value: Mapping[str, object]) -> str:
    normalized = _json_value(value)
    if not isinstance(normalized, dict):
        raise EntityConflictError("canonical evidence root must be an object")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def supplier_selection_evidence(
    *,
    decision: MatchingDecision | None,
    operator_id: UUID,
    rfq_id: UUID,
) -> dict[str, object]:
    selected = None
    if decision is not None:
        selected = next(
            (item for item in decision.matches if item.draft.operator_id.value == operator_id),
            None,
        )
    match: dict[str, object] | None = None
    if selected is not None:
        draft = selected.draft
        match = {
            "matching_rank": selected.rank,
            "aircraft_id": str(draft.aircraft_id),
            "aircraft_type_id": str(draft.aircraft_type_id),
            "reason_codes": [item.value for item in draft.reason_codes],
            "estimated_operating_cost_minor": draft.estimated_operating_cost.amount_minor,
            "estimated_operating_cost_currency": str(draft.estimated_operating_cost.currency),
            "budget_comparison": draft.budget_comparison.value,
            "budget_delta_minor": draft.budget_delta_minor,
            "route_distance_tenths_nm": draft.route_distance_tenths_nm,
            "required_range_nm": draft.required_range_nm,
            "reposition_distance_tenths_nm": draft.reposition_distance_tenths_nm,
            "route_minutes": draft.route_minutes,
            "reposition_minutes": draft.reposition_minutes,
            "timing_buffer_minutes": draft.timing_buffer_minutes,
            "schedule_risk_basis_points": draft.schedule_risk_basis_points,
            "score": {
                "method": selected.score.method,
                "total_basis_points": selected.score.total_basis_points,
                "deadhead_points": selected.score.deadhead_points,
                "operating_cost_points": selected.score.operating_cost_points,
                "timing_buffer_points": selected.score.timing_buffer_points,
                "schedule_risk_points": selected.score.schedule_risk_points,
            },
            "position": {
                "id": str(draft.position.id),
                "event_time": draft.position.event_time,
                "recorded_at": draft.position.recorded_at,
            },
            "availability": {
                "id": str(draft.availability.id),
                "valid_from": draft.availability.interval.start,
                "valid_to": draft.availability.interval.end,
                "recorded_at": draft.availability.recorded_at,
                "status": draft.availability.status.value,
            },
            "reference_profile": {
                "id": str(draft.reference_profile.id),
                "recorded_at": draft.reference_profile.recorded_at,
            },
        }
    return {
        "decision": "supplier_selected_for_rfq",
        "selection_policy_version": SUPPLIER_SELECTION_POLICY_VERSION,
        "rfq_id": str(rfq_id),
        "operator_id": str(operator_id),
        "matching_policy_version": decision.policy_version if decision is not None else None,
        "reference_currency": str(decision.reference_currency) if decision is not None else None,
        "known_as_of": decision.known_as_of if decision is not None else None,
        "position_event_cutoff": decision.position_event_cutoff if decision is not None else None,
        "candidate_count": decision.candidate_count if decision is not None else None,
        "feasible_count": decision.feasible_count if decision is not None else None,
        "matching_evidence_available": match is not None,
        "selection_basis": (
            "buyer_selected_with_current_matching_evidence"
            if match is not None
            else "buyer_selected_without_current_feasible_match"
        ),
        "match": match,
    }


def quote_comparison_evidence(
    *,
    comparison: MissionQuoteComparison,
    selected_quote_id: UUID,
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for entry in comparison.entries:
        suitability = entry.aircraft_suitability
        normalization = entry.normalization
        entries.append(
            {
                "quote_id": str(entry.quote.id),
                "operator_id": str(entry.operator_id),
                "aircraft_id": str(entry.quote.aircraft_id),
                "revision_number": entry.quote.revision_number,
                "quote_status": entry.quote.status.value,
                "valid_until": entry.quote.valid_until,
                "normalization_version": normalization.normalization_version,
                "currency": str(normalization.currency),
                "expected_total_minor": normalization.expected_total.amount_minor,
                "worst_case_total_minor": normalization.worst_case_total.amount_minor,
                "totals_complete": normalization.totals_complete,
                "pricing_confidence": normalization.confidence.value,
                "commercial_valid": entry.commercial_valid,
                "decision_eligible": entry.decision_eligible,
                "eligibility_reasons": [item.value for item in entry.eligibility_reasons],
                "currency_rank": entry.currency_rank,
                "aircraft_suitability": {
                    "feasible": suitability.feasible,
                    "reason_codes": [item.value for item in suitability.reason_codes],
                    "rejection_reasons": [item.value for item in suitability.rejection_reasons],
                    "seat_capacity": suitability.seat_capacity,
                    "aircraft_range_nm": suitability.aircraft_range_nm,
                    "required_range_nm": suitability.required_range_nm,
                    "reposition_distance_tenths_nm": suitability.reposition_distance_tenths_nm,
                    "timing_buffer_minutes": suitability.timing_buffer_minutes,
                    "schedule_risk_basis_points": suitability.schedule_risk_basis_points,
                    "position_event_time": suitability.position_event_time,
                    "position_recorded_at": suitability.position_recorded_at,
                    "availability_recorded_at": suitability.availability_recorded_at,
                    "reference_profile_recorded_at": suitability.reference_profile_recorded_at,
                },
                "score": {
                    "method": entry.score.method,
                    "total_basis_points": entry.score.total_basis_points,
                    "expected_total_points": entry.score.expected_total_points,
                    "worst_case_total_points": entry.score.worst_case_total_points,
                    "reposition_points": entry.score.reposition_points,
                    "operational_risk_points": entry.score.operational_risk_points,
                    "pricing_confidence_points": entry.score.pricing_confidence_points,
                    "currency_scope": str(entry.score.currency_scope),
                    "cohort_size": entry.score.cohort_size,
                },
            }
        )
    return {
        "decision": "quote_comparison_for_procurement_approval",
        "mission_id": str(comparison.mission_id),
        "selected_quote_id": str(selected_quote_id),
        "comparison_policy_version": comparison.comparison_policy_version,
        "matching_policy_version": comparison.matching_policy_version,
        "evaluated_at": comparison.evaluated_at,
        "pricing_currencies": [str(item) for item in comparison.pricing_currencies],
        "global_rank_available": comparison.global_rank_available,
        "quotes": entries,
        "no_implicit_fx": True,
    }


def _parsed_event(record: EvidenceEventRecord) -> tuple[dict[str, object], dict[str, object]]:
    try:
        decoded = cast(object, json.loads(record.canonical_json))
    except (json.JSONDecodeError, TypeError) as exc:
        raise EntityConflictError(f"event {record.event_id} has invalid canonical JSON") from exc
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise EntityConflictError(f"event {record.event_id} canonical JSON must be an object")
    event = cast(dict[str, object], decoded)

    expected: dict[str, object] = {
        "event_id": str(record.event_id),
        "aggregate_type": record.aggregate_type,
        "aggregate_id": str(record.aggregate_id),
        "aggregate_version": record.aggregate_version,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "occurred_at": _format_datetime(record.occurred_at),
        "recorded_at": _format_datetime(record.recorded_at),
        "actor_id": str(record.actor_id) if record.actor_id is not None else None,
        "correlation_id": str(record.correlation_id) if record.correlation_id is not None else None,
        "causation_id": str(record.causation_id) if record.causation_id is not None else None,
    }
    for key, expected_value in expected.items():
        if event.get(key) != expected_value:
            raise EntityConflictError(
                f"event {record.event_id} canonical envelope conflicts at {key}"
            )
    payload_obj = event.get("payload")
    if not isinstance(payload_obj, dict) or any(not isinstance(key, str) for key in payload_obj):
        raise EntityConflictError(f"event {record.event_id} payload must be an object")
    payload = cast(dict[str, object], payload_obj)
    return event, payload


def _public_event(
    record: EvidenceEventRecord,
    payload: Mapping[str, object],
) -> dict[str, JsonValue]:
    output = {
        key: _json_value(value)
        for key, value in payload.items()
        if key in _SAFE_EVENT_PAYLOAD_KEYS
    }
    return {
        "event_id": str(record.event_id),
        "aggregate_type": record.aggregate_type,
        "aggregate_id": str(record.aggregate_id),
        "aggregate_version": record.aggregate_version,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "occurred_at": _format_datetime(record.occurred_at),
        "recorded_at": _format_datetime(record.recorded_at),
        "actor_id": str(record.actor_id) if record.actor_id is not None else None,
        "correlation_id": str(record.correlation_id) if record.correlation_id is not None else None,
        "causation_id": str(record.causation_id) if record.causation_id is not None else None,
        "decision_output": output,
    }


def _public_source(record: EvidenceSourceRecord) -> dict[str, JsonValue]:
    facts = _json_value(record.facts)
    if not isinstance(facts, dict):
        raise EntityConflictError("source facts must serialize to an object")
    return {
        "source_type": record.source_type,
        "source_id": str(record.source_id),
        "version": record.version,
        "facts": facts,
    }


def _public_decision(record: DecisionEvidenceRecord) -> dict[str, JsonValue]:
    content = _json_value(record.content)
    policies = _json_value(record.policy_versions)
    if not isinstance(content, dict) or not isinstance(policies, dict):
        raise EntityConflictError("decision evidence must serialize to objects")
    return {
        "snapshot_id": str(record.snapshot_id),
        "decision_type": record.decision_type,
        "subject_type": record.subject_type,
        "subject_id": str(record.subject_id),
        "source_aggregate_type": record.source_aggregate_type,
        "source_aggregate_id": str(record.source_aggregate_id),
        "schema_version": record.schema_version,
        "decided_at": _format_datetime(record.decided_at),
        "known_as_of": (
            _format_datetime(record.known_as_of) if record.known_as_of is not None else None
        ),
        "actor_id": str(record.actor_id) if record.actor_id is not None else None,
        "correlation_id": (
            str(record.correlation_id) if record.correlation_id is not None else None
        ),
        "policy_versions": policies,
        "content": content,
        "integrity_digest": record.integrity_digest,
    }


class EvidenceService:
    def __init__(self, repository: EvidenceRepository) -> None:
        self._repository = repository

    def build(
        self,
        *,
        subject_type: EvidenceSubjectType,
        subject_id: UUID,
        party: EvidenceParty,
        event_limit: int,
        generated_at: datetime | None = None,
    ) -> EvidencePackage:
        if not 1 <= event_limit <= 500:
            raise ValueError("event_limit must be between 1 and 500")
        material = self._repository.load(
            subject_type=subject_type,
            subject_id=subject_id,
            party=party,
            event_limit=event_limit,
        )
        self._verify_events(material)
        self._verify_decisions(material)

        sources = tuple(
            _public_source(item)
            for item in sorted(
                material.sources,
                key=lambda item: (item.source_type, item.source_id.hex),
            )
        )
        parsed_events = []
        policy_versions: set[str] = set()
        for event in material.events:
            _, payload = _parsed_event(event)
            parsed_events.append(_public_event(event, payload))
            for key, value in payload.items():
                if key.endswith("_version") and isinstance(value, str):
                    policy_versions.add(value)

        decisions = tuple(
            _public_decision(item)
            for item in sorted(
                material.decisions,
                key=lambda item: (item.decided_at, item.snapshot_id.hex),
            )
        )
        for decision in material.decisions:
            for value in decision.policy_versions.values():
                if isinstance(value, str):
                    policy_versions.add(value)

        cutoff = max((event.recorded_at for event in material.events), default=None)
        completeness = (
            EvidenceCompleteness.TRUNCATED
            if material.truncated
            else EvidenceCompleteness.COMPLETE
        )
        diagnostics = list(material.diagnostics)
        if material.truncated:
            diagnostics.append(
                f"event lineage exceeded the requested bound of {event_limit}; "
                "package is intentionally not marked complete"
            )

        canonical_body: dict[str, object] = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "subject_type": subject_type.value,
            "subject_id": str(subject_id),
            "canonical_cutoff": cutoff,
            "sources": sources,
            "events": tuple(parsed_events),
            "decisions": decisions,
            "policy_versions": tuple(sorted(policy_versions)),
            "completeness": completeness.value,
            "diagnostics": tuple(diagnostics),
        }
        digest = canonical_digest(canonical_body)
        generated = generated_at or datetime.now(UTC)
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")

        return EvidencePackage(
            schema_version=PACKAGE_SCHEMA_VERSION,
            subject_type=subject_type,
            subject_id=subject_id,
            canonical_cutoff=cutoff,
            generated_at=generated.astimezone(UTC),
            sources=sources,
            events=tuple(parsed_events),
            decisions=decisions,
            policy_versions=tuple(sorted(policy_versions)),
            completeness=completeness,
            diagnostics=tuple(diagnostics),
            integrity_digest=digest,
        )

    @staticmethod
    def _verify_events(material: EvidenceMaterial) -> None:
        seen: set[UUID] = set()
        versions: dict[tuple[str, UUID], list[int]] = {}
        parsed_payloads: list[tuple[EvidenceEventRecord, dict[str, object]]] = []
        for event in material.events:
            if event.event_id in seen:
                raise EntityConflictError(f"duplicate event_id {event.event_id} in evidence")
            seen.add(event.event_id)
            _, payload = _parsed_event(event)
            parsed_payloads.append((event, payload))
            versions.setdefault(
                (event.aggregate_type, event.aggregate_id),
                [],
            ).append(event.aggregate_version)

        for (aggregate_type, aggregate_id), aggregate_versions in versions.items():
            ordered = sorted(aggregate_versions)
            expected = list(range(1, ordered[-1] + 1))
            if ordered != expected:
                raise EntityConflictError(
                    "event aggregate-version gap for "
                    f"{aggregate_type}:{aggregate_id}: {ordered}"
                )

        source_ids = {(source.source_type, source.source_id) for source in material.sources}
        commercial_change_ids = {
            source.source_id
            for source in material.sources
            if source.source_type == "disruption_commercial_change"
        }
        for event, payload in parsed_payloads:
            if event.event_type != "FINANCIAL_RECONCILIATION_OPENED":
                continue
            ids = payload.get("commercial_change_ids")
            if ids is None:
                continue
            if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                raise EntityConflictError(
                    f"reconciliation opening event {event.event_id} has invalid commercial lineage"
                )
            for item in ids:
                try:
                    change_id = UUID(item)
                except ValueError as exc:
                    raise EntityConflictError(
                        f"reconciliation opening event {event.event_id} has invalid change id"
                    ) from exc
                if change_id not in commercial_change_ids:
                    raise EntityConflictError(
                        "reconciliation baseline references missing disruption commercial "
                        f"change {change_id}"
                    )

        if material.truncated:
            return

        max_versions = {key: max(value) for key, value in versions.items()}
        for source in material.sources:
            if source.source_type not in _VERSIONED_SOURCE_TYPES or source.version is None:
                continue
            observed = max_versions.get((source.source_type, source.source_id))
            if observed is None:
                raise EntityConflictError(
                    f"missing event lineage for {source.source_type}:{source.source_id}"
                )
            if observed != source.version:
                raise EntityConflictError(
                    "canonical aggregate version conflicts with event lineage for "
                    f"{source.source_type}:{source.source_id}: "
                    f"row={source.version}, events={observed}"
                )

    @staticmethod
    def _verify_decisions(material: EvidenceMaterial) -> None:
        for decision in material.decisions:
            if decision.schema_version != SNAPSHOT_SCHEMA_VERSION:
                raise EntityConflictError(
                    f"unsupported decision evidence schema {decision.schema_version}"
                )
            body: dict[str, object] = {
                "schema_version": decision.schema_version,
                "decision_type": decision.decision_type,
                "subject_type": decision.subject_type,
                "subject_id": str(decision.subject_id),
                "source_aggregate_type": decision.source_aggregate_type,
                "source_aggregate_id": str(decision.source_aggregate_id),
                "decided_at": decision.decided_at,
                "known_as_of": decision.known_as_of,
                "actor_id": str(decision.actor_id) if decision.actor_id is not None else None,
                "correlation_id": (
                    str(decision.correlation_id)
                    if decision.correlation_id is not None
                    else None
                ),
                "policy_versions": dict(decision.policy_versions),
                "content": dict(decision.content),
            }
            expected = canonical_digest(body)
            if expected != decision.integrity_digest:
                raise EntityConflictError(
                    f"decision evidence digest mismatch for {decision.snapshot_id}"
                )
