import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.test_pr12_contracts_api import _contract_body, _setup_booking


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _create_accepted_contract(client: TestClient, *, booking_id: str, suffix: str) -> str:
    created = client.post(
        f"/v1/bookings/{booking_id}/contract",
        headers={"Idempotency-Key": f"pr13-contract-{suffix}"},
        json=_contract_body(),
    )
    assert created.status_code == 201
    contract_id = str(created.json()["id"])
    assert (
        client.post(
            f"/v1/contracts/{contract_id}/accept/buyer",
            headers={"Idempotency-Key": f"pr13-buyer-accept-{suffix}"},
        ).status_code
        == 200
    )
    accepted = client.post(
        f"/v1/contracts/{contract_id}/accept/operator",
        headers={"Idempotency-Key": f"pr13-operator-accept-{suffix}"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    return contract_id


@pytest.mark.integration
def test_booking_workflow_contract_guard_idempotency_mission_coupling_and_outbox() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, mission_id, _, _ = _setup_booking(client, suffix="EA")

        missing_contract = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr13-missing-contract-ea"},
        )
        assert missing_contract.status_code == 409
        assert client.get(f"/v1/bookings/{booking_id}").json()["state"] == "pending_contract"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "selected"

        created = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr13-contract-ea"},
            json=_contract_body(),
        )
        assert created.status_code == 201
        contract_id = str(created.json()["id"])
        assert (
            client.post(
                f"/v1/contracts/{contract_id}/accept/buyer",
                headers={"Idempotency-Key": "pr13-buyer-ea"},
            ).status_code
            == 200
        )

        partial_contract = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr13-partial-contract-ea"},
        )
        assert partial_contract.status_code == 409
        assert client.get(f"/v1/bookings/{booking_id}").json()["state"] == "pending_contract"

        accepted = client.post(
            f"/v1/contracts/{contract_id}/accept/operator",
            headers={"Idempotency-Key": "pr13-operator-ea"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"

        contracted = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr13-contracted-ea"},
        )
        assert contracted.status_code == 200
        assert contracted.json()["state"] == "contracted"
        assert contracted.json()["version"] == 2
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "contracting"

        replay = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr13-contracted-ea"},
        )
        assert replay.status_code == 200
        assert replay.json() == contracted.json()

        duplicate = client.post(
            f"/v1/bookings/{booking_id}/mark-contracted",
            headers={"Idempotency-Key": "pr13-contracted-ea-second-key"},
        )
        assert duplicate.status_code == 409

        commands = [
            ("mark-payment-pending", "payment_pending", "contracting"),
            ("confirm", "confirmed", "booked"),
            ("enter-pre-operation", "pre_operation", "booked"),
            ("start-operation", "operating", "operating"),
            ("complete", "completed", "completed"),
            ("reconcile", "reconciled", "completed"),
        ]
        for ordinal, (command, booking_state, mission_state) in enumerate(commands, start=1):
            response = client.post(
                f"/v1/bookings/{booking_id}/{command}",
                headers={"Idempotency-Key": f"pr13-{command}-ea-{ordinal}"},
            )
            assert response.status_code == 200
            assert response.json()["state"] == booking_state
            assert client.get(f"/v1/missions/{mission_id}").json()["status"] == mission_state

        final_booking = client.get(f"/v1/bookings/{booking_id}").json()
        assert final_booking["state"] == "reconciled"
        assert final_booking["version"] == 8

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            booking_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(booking_id)},
                )
                .scalars()
                .all()
            )
            assert booking_events == [
                "BOOKING_CREATED",
                "BOOKING_CONTRACTED",
                "BOOKING_PAYMENT_PENDING",
                "BOOKING_CONFIRMED",
                "BOOKING_PRE_OPERATION",
                "BOOKING_OPERATING",
                "BOOKING_COMPLETED",
                "BOOKING_RECONCILED",
            ]

            mission_workflow_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id AND event_type IN "
                        "('MISSION_CONTRACTING','MISSION_BOOKED','MISSION_OPERATING',"
                        "'MISSION_COMPLETED') ORDER BY aggregate_version"
                    ),
                    {"id": UUID(mission_id)},
                )
                .scalars()
                .all()
            )
            assert mission_workflow_events == [
                "MISSION_CONTRACTING",
                "MISSION_BOOKED",
                "MISSION_OPERATING",
                "MISSION_COMPLETED",
            ]

            persisted = connection.execute(
                text(
                    "SELECT state, version, state_changed_at, created_at "
                    "FROM bookings WHERE id=:id"
                ),
                {"id": UUID(booking_id)},
            ).one()
            assert persisted.state == "reconciled"
            assert persisted.version == 8
            assert persisted.state_changed_at >= persisted.created_at
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_same_booking_transition_advances_once() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="EB")
        _create_accepted_contract(client, booking_id=booking_id, suffix="eb")
        barrier = Barrier(2)

        def contract_once(key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/bookings/{booking_id}/mark-contracted",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(contract_once, "pr13-race-eb-1")
            second = executor.submit(contract_once, "pr13-race-eb-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [200, 409]
        final = client.get(f"/v1/bookings/{booking_id}")
        assert final.status_code == 200
        assert final.json()["state"] == "contracted"
        assert final.json()["version"] == 2

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            transition_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE aggregate_id=:id AND event_type='BOOKING_CONTRACTED'"
                ),
                {"id": UUID(booking_id)},
            ).scalar_one()
            assert transition_events == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_competing_next_step_commands_serialize_without_skipping_state() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="ED")
        _create_accepted_contract(client, booking_id=booking_id, suffix="ed")
        barrier = Barrier(2)

        def invoke(command: str, key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/bookings/{booking_id}/{command}",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            contracted_future = executor.submit(
                invoke, "mark-contracted", "pr13-compete-contract-ed"
            )
            payment_future = executor.submit(
                invoke, "mark-payment-pending", "pr13-compete-payment-ed"
            )
            contracted_status = contracted_future.result()
            payment_status = payment_future.result()

        assert contracted_status == 200
        assert payment_status in (200, 409)
        final = client.get(f"/v1/bookings/{booking_id}").json()
        expected_state = "payment_pending" if payment_status == 200 else "contracted"
        assert final["state"] == expected_state
        assert final["version"] == (3 if payment_status == 200 else 2)

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT event_type FROM outbox_events WHERE aggregate_id=:id "
                    "AND event_type LIKE 'BOOKING_%' ORDER BY aggregate_version"
                ),
                {"id": UUID(booking_id)},
            ).scalars().all()
            assert rows[:2] == ["BOOKING_CREATED", "BOOKING_CONTRACTED"]
            assert rows.count("BOOKING_CONTRACTED") == 1
            assert rows.count("BOOKING_PAYMENT_PENDING") == (1 if payment_status == 200 else 0)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_contract_acceptance_and_contracting_race_cannot_bypass_acceptance_guard() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, _, _ = _setup_booking(client, suffix="EC")
        created = client.post(
            f"/v1/bookings/{booking_id}/contract",
            headers={"Idempotency-Key": "pr13-contract-ec"},
            json=_contract_body(),
        )
        assert created.status_code == 201
        contract_id = str(created.json()["id"])
        assert (
            client.post(
                f"/v1/contracts/{contract_id}/accept/buyer",
                headers={"Idempotency-Key": "pr13-buyer-ec"},
            ).status_code
            == 200
        )
        barrier = Barrier(2)

        def accept_operator() -> int:
            barrier.wait()
            response = client.post(
                f"/v1/contracts/{contract_id}/accept/operator",
                headers={"Idempotency-Key": "pr13-race-operator-ec"},
            )
            return int(response.status_code)

        def mark_contracted() -> int:
            barrier.wait()
            response = client.post(
                f"/v1/bookings/{booking_id}/mark-contracted",
                headers={"Idempotency-Key": "pr13-race-contract-ec"},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            operator_future = executor.submit(accept_operator)
            booking_future = executor.submit(mark_contracted)
            operator_status = operator_future.result()
            booking_status = booking_future.result()

        assert operator_status == 200
        assert booking_status in (200, 409)
        assert client.get(f"/v1/contracts/{contract_id}").json()["status"] == "accepted"

        if booking_status == 409:
            retry = client.post(
                f"/v1/bookings/{booking_id}/mark-contracted",
                headers={"Idempotency-Key": "pr13-race-contract-ec-retry"},
            )
            assert retry.status_code == 200

        final = client.get(f"/v1/bookings/{booking_id}")
        assert final.status_code == 200
        assert final.json()["state"] == "contracted"
        assert final.json()["version"] == 2

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            transition_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE aggregate_id=:id AND event_type='BOOKING_CONTRACTED'"
                ),
                {"id": UUID(booking_id)},
            ).scalar_one()
            assert transition_events == 1
            contract_accepted_at = connection.execute(
                text("SELECT accepted_at FROM contracts WHERE id=:id"),
                {"id": UUID(contract_id)},
            ).scalar_one()
            booking_contracted_at = connection.execute(
                text(
                    "SELECT occurred_at FROM outbox_events WHERE aggregate_id=:id "
                    "AND event_type='BOOKING_CONTRACTED'"
                ),
                {"id": UUID(booking_id)},
            ).scalar_one()
            assert booking_contracted_at >= contract_accepted_at
    finally:
        engine.dispose()
