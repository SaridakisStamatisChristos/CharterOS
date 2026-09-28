from uuid import UUID

import pytest

from charteros.domain.shared import DomainValidationError, TypedId


class MissionId(TypedId):
    __slots__ = ()


class QuoteId(TypedId):
    __slots__ = ()


def test_typed_ids_preserve_runtime_type_separation() -> None:
    raw = "12345678-1234-5678-1234-567812345678"
    mission_id = MissionId.parse(raw)
    quote_id = QuoteId.parse(raw)

    assert mission_id.value == UUID(raw)
    assert str(mission_id) == raw
    assert mission_id.__eq__(quote_id) is False


def test_typed_id_rejects_invalid_uuid() -> None:
    with pytest.raises(DomainValidationError):
        MissionId.parse("not-a-uuid")
