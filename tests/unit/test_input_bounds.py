from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.routes.buyer_portal import BuyerMissionCreate
from apps.api.routes.contracts import CreateContractRequest
from apps.api.routes.missions import MissionCreate
from apps.api.routes.quotes import QuoteTermsRequest
from charteros.application.resource_limits import (
    MAX_OPTIMIZATION_WINDOW,
    MAX_TIMELINE_WINDOW,
    validate_bounded_window,
)
from charteros.domain.shared.exceptions import DomainValidationError


def _mission_payload() -> dict[str, object]:
    start = datetime.now(UTC) + timedelta(days=7)
    return {
        "buyer_id": str(uuid4()),
        "origin_airport_id": str(uuid4()),
        "destination_airport_id": str(uuid4()),
        "departure_window": {
            "start": start.isoformat(),
            "end": (start + timedelta(hours=2)).isoformat(),
        },
        "passenger_count": 4,
        "special_requirements": [],
    }


def test_mission_requirement_collection_rejects_oversize_item_without_truncation() -> None:
    payload = _mission_payload()
    payload["special_requirements"] = ["x" * 501]

    with pytest.raises(ValidationError):
        MissionCreate.model_validate(payload)

    buyer_payload = dict(payload)
    buyer_payload.pop("buyer_id")
    with pytest.raises(ValidationError):
        BuyerMissionCreate.model_validate(buyer_payload)


def test_mission_requirement_collection_rejects_excess_cardinality() -> None:
    payload = _mission_payload()
    payload["special_requirements"] = ["bounded"] * 65

    with pytest.raises(ValidationError):
        MissionCreate.model_validate(payload)


def test_quote_terms_reject_oversize_collection_items_and_cardinality() -> None:
    now = datetime.now(UTC)
    base = {
        "aircraft_id": str(uuid4()),
        "currency": "EUR",
        "base_amount_minor": 1_000_000,
        "valid_until": (now + timedelta(days=2)).isoformat(),
    }

    with pytest.raises(ValidationError):
        QuoteTermsRequest.model_validate({**base, "inclusions": ["x" * 1001]})

    with pytest.raises(ValidationError):
        QuoteTermsRequest.model_validate({**base, "exclusions": ["ok"] * 129})


def test_contract_metadata_rejects_excess_cardinality_and_value_size() -> None:
    with pytest.raises(ValidationError):
        CreateContractRequest.model_validate(
            {
                "document_reference": "contract-ref",
                "document_version": 1,
                "metadata": {f"k-{index}": "v" for index in range(129)},
            }
        )

    with pytest.raises(ValidationError):
        CreateContractRequest.model_validate(
            {
                "document_reference": "contract-ref",
                "document_version": 1,
                "metadata": {"bounded-key": "x" * 2001},
            }
        )


def test_query_windows_fail_explicitly_beyond_validated_horizon() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    validate_bounded_window(
        start=start,
        end=start + MAX_TIMELINE_WINDOW,
        maximum=MAX_TIMELINE_WINDOW,
        name="timeline",
    )

    with pytest.raises(DomainValidationError, match="maximum duration"):
        validate_bounded_window(
            start=start,
            end=start + MAX_TIMELINE_WINDOW + timedelta(seconds=1),
            maximum=MAX_TIMELINE_WINDOW,
            name="timeline",
        )

    with pytest.raises(DomainValidationError, match="maximum duration"):
        validate_bounded_window(
            start=start,
            end=start + MAX_OPTIMIZATION_WINDOW + timedelta(seconds=1),
            maximum=MAX_OPTIMIZATION_WINDOW,
            name="optimizer",
        )
