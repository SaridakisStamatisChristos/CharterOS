from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from charteros.application.evidence import (
    SNAPSHOT_SCHEMA_VERSION,
    DecisionEvidenceRecord,
    EvidenceEventRecord,
    EvidenceMaterial,
    EvidenceParty,
    EvidenceService,
    EvidenceSourceRecord,
    EvidenceSubjectType,
)
from charteros.application.exceptions import EntityConflictError

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
MISSION_ID = UUID(int=1)
BUYER_ID = UUID(int=2)


class FakeEvidenceRepository:
    def __init__(self, material: EvidenceMaterial) -> None:
        self.material = material

    def load(
        self,
        *,
        subject_type: EvidenceSubjectType,
        subject_id: UUID,
        party: EvidenceParty,
        event_limit: int,
    ) -> EvidenceMaterial:
        assert subject_type is EvidenceSubjectType.MISSION
        assert subject_id == MISSION_ID
        assert party.buyer_id == BUYER_ID
        assert event_limit > 0
        return self.material


def _event(
    *,
    version: int = 1,
    event_id: UUID | None = None,
    canonical_aggregate_version: int | None = None,
) -> EvidenceEventRecord:
    identifier = event_id or UUID(int=100 + version)
    occurred_at = NOW + timedelta(minutes=version)
    recorded_at = occurred_at + timedelta(seconds=1)
    payload: dict[str, object] = {
        "status": "open",
        "matching_policy_version": "matching-v1",
    }
    canonical: dict[str, object] = {
        "event_id": str(identifier),
        "aggregate_type": "mission",
        "aggregate_id": str(MISSION_ID),
        "aggregate_version": (
            version if canonical_aggregate_version is None else canonical_aggregate_version
        ),
        "event_type": "MISSION_OPENED",
        "event_version": 1,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "actor_id": None,
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }
    return EvidenceEventRecord(
        event_id=identifier,
        aggregate_type="mission",
        aggregate_id=MISSION_ID,
        aggregate_version=version,
        event_type="MISSION_OPENED",
        event_version=1,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        actor_id=None,
        correlation_id=None,
        causation_id=None,
        canonical_json=json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _source(version: int = 1) -> EvidenceSourceRecord:
    return EvidenceSourceRecord(
        source_type="mission",
        source_id=MISSION_ID,
        version=version,
        facts={"buyer_id": BUYER_ID, "status": "open"},
    )


def _material(
    *,
    events: tuple[EvidenceEventRecord, ...] | None = None,
    version: int = 1,
    decisions: tuple[DecisionEvidenceRecord, ...] = (),
    truncated: bool = False,
) -> EvidenceMaterial:
    return EvidenceMaterial(
        sources=(_source(version),),
        events=events if events is not None else (_event(),),
        decisions=decisions,
        truncated=truncated,
    )


def test_same_source_state_has_stable_digest_and_canonical_content() -> None:
    service = EvidenceService(FakeEvidenceRepository(_material()))

    first = service.build(
        subject_type=EvidenceSubjectType.MISSION,
        subject_id=MISSION_ID,
        party=EvidenceParty(buyer_id=BUYER_ID),
        event_limit=100,
        generated_at=NOW,
    )
    second = service.build(
        subject_type=EvidenceSubjectType.MISSION,
        subject_id=MISSION_ID,
        party=EvidenceParty(buyer_id=BUYER_ID),
        event_limit=100,
        generated_at=NOW + timedelta(hours=1),
    )

    assert first.integrity_digest == second.integrity_digest
    assert first.generated_at != second.generated_at
    assert first.events == second.events
    assert first.sources == second.sources
    assert first.completeness.value == "complete"
    assert "matching-v1" in first.policy_versions


def test_event_aggregate_version_gap_fails_explicitly() -> None:
    material = _material(
        events=(
            _event(version=1),
            _event(version=3),
        ),
        version=3,
    )

    with pytest.raises(EntityConflictError, match="aggregate-version gap"):
        EvidenceService(FakeEvidenceRepository(material)).build(
            subject_type=EvidenceSubjectType.MISSION,
            subject_id=MISSION_ID,
            party=EvidenceParty(buyer_id=BUYER_ID),
            event_limit=100,
        )


def test_missing_terminal_event_fails_against_canonical_source_version() -> None:
    material = _material(events=(_event(version=1),), version=2)

    with pytest.raises(EntityConflictError, match="canonical aggregate version conflicts"):
        EvidenceService(FakeEvidenceRepository(material)).build(
            subject_type=EvidenceSubjectType.MISSION,
            subject_id=MISSION_ID,
            party=EvidenceParty(buyer_id=BUYER_ID),
            event_limit=100,
        )


def test_canonical_event_envelope_conflict_fails_explicitly() -> None:
    material = _material(
        events=(
            _event(
                version=1,
                canonical_aggregate_version=99,
            ),
        )
    )

    with pytest.raises(EntityConflictError, match="canonical envelope conflicts"):
        EvidenceService(FakeEvidenceRepository(material)).build(
            subject_type=EvidenceSubjectType.MISSION,
            subject_id=MISSION_ID,
            party=EvidenceParty(buyer_id=BUYER_ID),
            event_limit=100,
        )


def test_decision_snapshot_digest_mismatch_fails_explicitly() -> None:
    decision = DecisionEvidenceRecord(
        snapshot_id=UUID(int=50),
        decision_type="supplier_selection",
        subject_type="mission",
        subject_id=MISSION_ID,
        source_aggregate_type="rfq",
        source_aggregate_id=UUID(int=60),
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        decided_at=NOW,
        known_as_of=NOW,
        actor_id=BUYER_ID,
        correlation_id=UUID(int=70),
        policy_versions={"matching": "matching-v1"},
        content={"decision": "supplier_selected_for_rfq"},
        integrity_digest="0" * 64,
    )

    with pytest.raises(EntityConflictError, match="digest mismatch"):
        EvidenceService(FakeEvidenceRepository(_material(decisions=(decision,)))).build(
            subject_type=EvidenceSubjectType.MISSION,
            subject_id=MISSION_ID,
            party=EvidenceParty(buyer_id=BUYER_ID),
            event_limit=100,
        )


def test_truncated_package_is_never_marked_complete() -> None:
    package = EvidenceService(FakeEvidenceRepository(_material(truncated=True))).build(
        subject_type=EvidenceSubjectType.MISSION,
        subject_id=MISSION_ID,
        party=EvidenceParty(buyer_id=BUYER_ID),
        event_limit=1,
    )

    assert package.completeness.value == "truncated"
    assert package.diagnostics
