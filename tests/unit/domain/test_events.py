from datetime import UTC, datetime

import pytest

from charteros.domain.shared import DomainEvent, DomainValidationError, EventId, TypedId


class MissionId(TypedId):
    __slots__ = ()


def _event(payload: dict[str, object]) -> DomainEvent:
    return DomainEvent(
        event_id=EventId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        aggregate_type="mission",
        aggregate_id=MissionId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        aggregate_version=3,
        event_type="MISSION_OPENED",
        event_version=1,
        occurred_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        recorded_at=datetime(2026, 9, 28, 12, 0, 1, tzinfo=UTC),
        payload=payload,
    )


def test_event_serialization_is_deterministic_and_payload_is_snapshotted() -> None:
    payload: dict[str, object] = {"b": [2, 1], "a": {"z": True, "x": None}}
    event = _event(payload)
    same_event = _event({"a": {"x": None, "z": True}, "b": [2, 1]})

    assert event.to_json() == same_event.to_json()

    nested = payload["b"]
    assert isinstance(nested, list)
    nested.append(99)
    assert event.to_json() == same_event.to_json()


def test_event_rejects_recorded_time_before_occurrence() -> None:
    with pytest.raises(DomainValidationError):
        DomainEvent(
            event_id=EventId.new(),
            aggregate_type="mission",
            aggregate_id=MissionId.new(),
            aggregate_version=1,
            event_type="MISSION_CREATED",
            event_version=1,
            occurred_at=datetime(2026, 9, 28, 12, 0, 1, tzinfo=UTC),
            recorded_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        )
