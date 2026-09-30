from pydantic import TypeAdapter

from apps.api.routes.repositioning import EmptyLegLimit
from charteros.application.repositioning import MAX_STRUCTURAL_EMPTY_LEGS


def test_reposition_solver_supported_empty_leg_cap_is_100() -> None:
    assert MAX_STRUCTURAL_EMPTY_LEGS == 100


def test_api_empty_leg_limit_matches_application_capacity() -> None:
    schema = TypeAdapter(EmptyLegLimit).json_schema()

    assert schema["minimum"] == 1
    assert schema["maximum"] == MAX_STRUCTURAL_EMPTY_LEGS
