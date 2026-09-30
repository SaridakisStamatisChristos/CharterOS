from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.test_pr12_contracts_api import _setup_booking
from tests.integration.test_pr13_booking_workflow_api import _create_accepted_contract
from tests.integration.test_pr38_aircraft_capacity import (
    _create_quote,
    _setup_shared_aircraft,
)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _reservation_for_booking(settings: Settings, booking_id: str) -> dict[str, object]:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT id, version, aircraft_id, booking_id, mission_id, status,
                           released_at, release_reason
                    FROM aircraft_capacity_reservations
                    WHERE booking_id = :booking_id
                    """
                    ),
                    {"booking_id": UUID(booking_id)},
                )
                .mappings()
                .one()
            )
            return dict(row)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr39_contract_unsigned_expiry_releases_tail_for_overlapping_award() -> None:
    settings = _settings()
    departure = datetime.now(UTC) + timedelta(days=7)
    with TestClient(create_app(settings)) as client:
        shared = _setup_shared_aircraft(client, suffix="RA")
        mission_a, quote_a = _create_quote(
            client,
            shared=shared,
            suffix="RA1",
            departure=departure,
        )
        mission_b, quote_b = _create_quote(
            client,
            shared=shared,
            suffix="RA2",
            departure=departure + timedelta(minutes=30),
        )

        first_award = client.post(
            f"/v1/quotes/{quote_a}/accept",
            headers={"Idempotency-Key": "pr39-first-award-ra"},
        )
        assert first_award.status_code == 201
        booking_id = str(first_award.json()["id"])

        blocked = client.post(
            f"/v1/quotes/{quote_b}/accept",
            headers={"Idempotency-Key": "pr39-blocked-award-ra"},
        )
        assert blocked.status_code == 409

        expired = client.post(
            f"/v1/bookings/{booking_id}/expire",
            headers={"Idempotency-Key": "pr39-expire-ra"},
            json={"reason": "contract_unsigned"},
        )
        assert expired.status_code == 200
        assert expired.json()["state"] == "expired"
        assert expired.json()["termination_reason"] == "contract_unsigned"
        assert expired.json()["termination_source"] == "system"
        assert client.get(f"/v1/missions/{mission_a}").json()["status"] == "expired"

        replay = client.post(
            f"/v1/bookings/{booking_id}/expire",
            headers={"Idempotency-Key": "pr39-expire-ra"},
            json={"reason": "contract_unsigned"},
        )
        assert replay.status_code == 200
        assert replay.json() == expired.json()

        body_conflict = client.post(
            f"/v1/bookings/{booking_id}/expire",
            headers={"Idempotency-Key": "pr39-expire-ra"},
            json={"reason": "commercial_expiry"},
        )
        assert body_conflict.status_code == 409

        second_award = client.post(
            f"/v1/quotes/{quote_b}/accept",
            headers={"Idempotency-Key": "pr39-second-award-ra"},
        )
        assert second_award.status_code == 201
        assert client.get(f"/v1/missions/{mission_b}").json()["status"] == "selected"

    released = _reservation_for_booking(settings, booking_id)
    assert released["status"] == "released"
    assert released["version"] == 2
    assert released["release_reason"] == "contract_unsigned"
    assert released["released_at"] is not None


@pytest.mark.integration
def test_pr39_contracted_buyer_cancel_is_atomic_with_capacity_release() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, mission_id, _, _ = _setup_booking(client, suffix="RB")
        _create_accepted_contract(client, booking_id=booking_id, suffix="rb")
        contracted = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr39-contract-rb"},
        )
        assert contracted.status_code == 200

        cancelled = client.post(
            f"/v1/bookings/{booking_id}/cancel",
            headers={"Idempotency-Key": "pr39-cancel-rb"},
            json={"reason": "buyer_cancel"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["termination_reason"] == "buyer_cancel"
        assert cancelled.json()["termination_source"] == "buyer"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "cancelled"

    reservation = _reservation_for_booking(settings, booking_id)
    assert reservation["status"] == "released"
    assert reservation["release_reason"] == "buyer_cancel"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            booking_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events WHERE aggregate_id = :id "
                        "AND event_type = 'BOOKING_CANCELLED'"
                    ),
                    {"id": UUID(booking_id)},
                )
                .scalars()
                .all()
            )
            mission_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events WHERE aggregate_id = :id "
                        "AND event_type = 'MISSION_CANCELLED'"
                    ),
                    {"id": UUID(mission_id)},
                )
                .scalars()
                .all()
            )
            reservation_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events WHERE aggregate_id = :id "
                        "AND event_type = 'AIRCRAFT_CAPACITY_RELEASED'"
                    ),
                    {"id": reservation["id"]},
                )
                .scalars()
                .all()
            )
            assert booking_events == ["BOOKING_CANCELLED"]
            assert mission_events == ["MISSION_CANCELLED"]
            assert reservation_events == ["AIRCRAFT_CAPACITY_RELEASED"]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr39_payment_timeout_expires_payment_pending_booking() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, mission_id, _, _ = _setup_booking(client, suffix="RC")
        _create_accepted_contract(client, booking_id=booking_id, suffix="rc")
        assert (
            client.post(
                f"/v1/bookings/{booking_id}/mark-contracted",
                headers={"Idempotency-Key": "pr39-contract-rc"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/bookings/{booking_id}/mark-payment-pending",
                headers={"Idempotency-Key": "pr39-payment-rc"},
            ).status_code
            == 200
        )

        expired = client.post(
            f"/v1/bookings/{booking_id}/expire",
            headers={"Idempotency-Key": "pr39-expire-rc"},
            json={"reason": "deposit_timeout"},
        )
        assert expired.status_code == 200
        assert expired.json()["state"] == "expired"
        assert expired.json()["termination_reason"] == "deposit_timeout"
        assert expired.json()["termination_source"] == "system"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "expired"

    reservation = _reservation_for_booking(settings, booking_id)
    assert reservation["status"] == "released"
    assert reservation["release_reason"] == "deposit_timeout"


@pytest.mark.integration
def test_pr39_invalid_reason_state_and_confirmed_release_fail_closed() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="RD")

        invalid_timeout = client.post(
            f"/v1/bookings/{booking_id}/expire",
            headers={"Idempotency-Key": "pr39-invalid-timeout-rd"},
            json={"reason": "deposit_timeout"},
        )
        assert invalid_timeout.status_code == 409
        assert _reservation_for_booking(settings, booking_id)["status"] == "reserved"

        _create_accepted_contract(client, booking_id=booking_id, suffix="rd")
        assert (
            client.post(
                f"/v1/bookings/{booking_id}/mark-contracted",
                headers={"Idempotency-Key": "pr39-contract-rd"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/bookings/{booking_id}/mark-payment-pending",
                headers={"Idempotency-Key": "pr39-payment-rd"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/bookings/{booking_id}/confirm",
                headers={"Idempotency-Key": "pr39-confirm-rd"},
            ).status_code
            == 200
        )

        too_late = client.post(
            f"/v1/bookings/{booking_id}/cancel",
            headers={"Idempotency-Key": "pr39-too-late-rd"},
            json={"reason": "operator_release"},
        )
        assert too_late.status_code == 409
        assert _reservation_for_booking(settings, booking_id)["status"] == "reserved"


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr39_competing_terminal_commands_release_capacity_exactly_once() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, mission_id, _, _ = _setup_booking(client, suffix="RE")
        barrier = Barrier(2)

        def invoke(path: str, key: str, reason: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/bookings/{booking_id}/{path}",
                headers={"Idempotency-Key": key},
                json={"reason": reason},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            cancel_future = executor.submit(
                invoke,
                "cancel",
                "pr39-race-cancel-re",
                "buyer_cancel",
            )
            expire_future = executor.submit(
                invoke,
                "expire",
                "pr39-race-expire-re",
                "contract_unsigned",
            )
            statuses = sorted((cancel_future.result(), expire_future.result()))

        assert statuses == [200, 409]
        final = client.get(f"/v1/bookings/{booking_id}")
        assert final.status_code == 200
        assert final.json()["state"] in {"cancelled", "expired"}
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] in {
            "cancelled",
            "expired",
        }

    reservation = _reservation_for_booking(settings, booking_id)
    assert reservation["status"] == "released"
    assert reservation["version"] == 2

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            terminal_booking_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id = :id "
                    "AND event_type IN ('BOOKING_CANCELLED','BOOKING_EXPIRED')"
                ),
                {"id": UUID(booking_id)},
            ).scalar_one()
            release_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id = :id "
                    "AND event_type = 'AIRCRAFT_CAPACITY_RELEASED'"
                ),
                {"id": reservation["id"]},
            ).scalar_one()
            assert terminal_booking_events == 1
            assert release_events == 1
    finally:
        engine.dispose()
