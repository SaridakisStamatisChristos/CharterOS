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


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _create_operator(client: TestClient, *, suffix: str) -> str:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr8-operator-org-{suffix}"},
        json={
            "type": "operator",
            "legal_name": f"PR8 Operator {suffix}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr8-operator-{suffix}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR8-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    return str(operator.json()["id"])


def _create_aircraft(
    client: TestClient,
    *,
    suffix: str,
    operator_id: str,
    home_base: str,
) -> str:
    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr8-aircraft-{suffix}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-Q{suffix}",
            "aircraft_type": {
                "manufacturer": "PR8 Airframes",
                "model": f"QuoteJet {suffix}",
                "category": "regional",
                "seats_min": 1,
                "seats_max": 120,
                "range_nm": 3000,
                "runway_requirements": {},
                "baggage_cargo_profile": {},
            },
            "seat_capacity": 90,
            "cargo_capacity": "1000",
            "range_nm": 2500,
            "home_base": home_base,
            "status": "active",
        },
    )
    assert aircraft.status_code == 201
    return str(aircraft.json()["id"])


def _setup_rfq(
    client: TestClient,
    *,
    suffix: str,
    acknowledge: bool = True,
) -> tuple[str, str, str, str, datetime, str]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr8-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR8 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201
    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr8-origin-{suffix}"},
        json={
            "icao": f"Q{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr8-destination-{suffix}"},
        json={
            "icao": f"Q{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    operator_id = _create_operator(client, suffix=suffix)
    aircraft_id = _create_aircraft(
        client,
        suffix=suffix,
        operator_id=operator_id,
        home_base=str(origin.json()["id"]),
    )
    departure = datetime.now(UTC) + timedelta(days=7)
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr8-mission-{suffix}"},
        json={
            "buyer_id": buyer.json()["id"],
            "origin_airport_id": origin.json()["id"],
            "destination_airport_id": destination.json()["id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=3)).isoformat(),
            },
            "passenger_count": 80,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr8-open-{suffix}"},
    )
    assert opened.status_code == 200

    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr8-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    rfq_id = str(rfq.json()["id"])
    if acknowledge:
        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": f"pr8-ack-{suffix}"},
        )
        assert acknowledged.status_code == 200
    return (
        mission_id,
        rfq_id,
        operator_id,
        aircraft_id,
        departure,
        str(origin.json()["id"]),
    )


def _quote_body(
    aircraft_id: str,
    valid_until: datetime,
    *,
    base_amount_minor: int = 7_400_000,
) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": base_amount_minor,
        "repositioning_amount_minor": 300_000,
        "price_components": [
            {
                "category": "handling",
                "label": "Airport handling",
                "amount_minor": 125_000,
            },
            {
                "category": "taxes",
                "label": "Taxes",
                "amount_minor": 50_000,
                "condition": "Known taxes at submission",
            },
        ],
        "inclusions": [" Catering ", "WiFi"],
        "exclusions": ["Deicing"],
        "cancellation_terms": "25% until 72 hours",
        "payment_terms": "50% on confirmation",
        "valid_until": valid_until.isoformat(),
    }


@pytest.mark.integration
def test_quote_submission_is_structured_idempotent_and_transitions_rfq() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, rfq_id, _, aircraft_id, departure, _ = _setup_rfq(
            client,
            suffix="QA",
        )
        body = _quote_body(aircraft_id, departure - timedelta(days=1))
        created = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-submit-qa"},
            json=body,
        )
        assert created.status_code == 201
        quote = created.json()
        quote_id = quote["id"]
        assert quote["status"] == "submitted"
        assert quote["version"] == 1
        assert quote["revision_number"] == 1
        assert quote["submitted_total"]["amount_minor"] == 7_875_000
        assert quote["submitted_total"]["currency"] == "EUR"
        assert quote["inclusions"] == ["Catering", "WiFi"]
        assert [item["category"] for item in quote["price_components"]] == [
            "handling",
            "taxes",
        ]

        replay = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-submit-qa"},
            json=body,
        )
        assert replay.status_code == 201
        assert replay.json() == quote

        changed = _quote_body(
            aircraft_id,
            departure - timedelta(days=1),
            base_amount_minor=7_300_000,
        )
        conflict = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-submit-qa"},
            json=changed,
        )
        assert conflict.status_code == 409

        listed = client.get(f"/v1/rfqs/{rfq_id}/quotes")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["quotes"]] == [quote_id]
        fetched = client.get(f"/v1/quotes/{quote_id}")
        assert fetched.status_code == 200
        assert fetched.json() == quote

        rfqs = client.get(f"/v1/missions/{mission_id}/rfqs")
        assert rfqs.status_code == 200
        persisted_rfq = rfqs.json()["rfqs"][0]
        assert persisted_rfq["status"] == "quoted"
        assert persisted_rfq["version"] == 4

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            quote_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :aggregate_id ORDER BY aggregate_version"
                    ),
                    {"aggregate_id": UUID(quote_id)},
                )
                .scalars()
                .all()
            )
            assert quote_events == ["QUOTE_SUBMITTED"]
            rfq_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :aggregate_id ORDER BY aggregate_version"
                    ),
                    {"aggregate_id": UUID(rfq_id)},
                )
                .scalars()
                .all()
            )
            assert rfq_events == [
                "RFQ_CREATED",
                "RFQ_SENT",
                "RFQ_ACKNOWLEDGED",
                "RFQ_QUOTED",
            ]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_quote_submission_rejects_invalid_rfq_and_aircraft_association() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, rfq_id, operator_id, aircraft_id, departure, origin_id = _setup_rfq(
            client,
            suffix="QB",
        )
        body = _quote_body(aircraft_id, departure - timedelta(days=1))

        unknown_rfq = client.post(
            f"/v1/rfqs/{UUID(int=999001)}/quotes",
            headers={"Idempotency-Key": "pr8-unknown-rfq"},
            json=body,
        )
        assert unknown_rfq.status_code == 404

        unknown_aircraft_body = _quote_body(
            str(UUID(int=999002)),
            departure - timedelta(days=1),
        )
        unknown_aircraft = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-unknown-aircraft"},
            json=unknown_aircraft_body,
        )
        assert unknown_aircraft.status_code == 404

        second_operator = _create_operator(client, suffix="QC")
        foreign_aircraft = _create_aircraft(
            client,
            suffix="QC",
            operator_id=second_operator,
            home_base=origin_id,
        )
        wrong_operator = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-wrong-aircraft"},
            json=_quote_body(foreign_aircraft, departure - timedelta(days=1)),
        )
        assert wrong_operator.status_code == 409

        assert operator_id != second_operator


@pytest.mark.integration
def test_quote_requires_acknowledged_live_rfq_and_valid_window() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, sent_rfq_id, _, sent_aircraft, departure, _ = _setup_rfq(
            client,
            suffix="QD",
            acknowledge=False,
        )
        not_acknowledged = client.post(
            f"/v1/rfqs/{sent_rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-not-acked"},
            json=_quote_body(sent_aircraft, departure - timedelta(days=1)),
        )
        assert not_acknowledged.status_code == 409

        _, terminal_rfq_id, _, terminal_aircraft, terminal_departure, _ = _setup_rfq(
            client,
            suffix="QE",
        )
        declined = client.post(
            f"/v1/rfqs/{terminal_rfq_id}/decline",
            headers={"Idempotency-Key": "pr8-decline-qe"},
            json={"reason": "No capacity"},
        )
        assert declined.status_code == 200
        terminal = client.post(
            f"/v1/rfqs/{terminal_rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-terminal-qe"},
            json=_quote_body(terminal_aircraft, terminal_departure - timedelta(days=1)),
        )
        assert terminal.status_code == 409

        _, deadline_rfq_id, _, deadline_aircraft, deadline_departure, _ = _setup_rfq(
            client,
            suffix="QF",
        )
        engine = create_engine(settings.database_url)
        try:
            now = datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE rfqs SET created_at = :created_at, sent_at = :sent_at, "
                        "acknowledged_at = :acknowledged_at, response_deadline = :deadline "
                        "WHERE id = :rfq_id"
                    ),
                    {
                        "created_at": now - timedelta(hours=4),
                        "sent_at": now - timedelta(hours=3),
                        "acknowledged_at": now - timedelta(hours=2),
                        "deadline": now - timedelta(hours=1),
                        "rfq_id": UUID(deadline_rfq_id),
                    },
                )
        finally:
            engine.dispose()
        after_deadline = client.post(
            f"/v1/rfqs/{deadline_rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-after-deadline"},
            json=_quote_body(deadline_aircraft, deadline_departure - timedelta(days=1)),
        )
        assert after_deadline.status_code == 409

        invalid_validity = client.post(
            f"/v1/rfqs/{deadline_rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-invalid-validity"},
            json=_quote_body(deadline_aircraft, deadline_departure + timedelta(hours=1)),
        )
        assert invalid_validity.status_code == 409


@pytest.mark.integration
def test_revision_preserves_history_and_emits_lineage_events() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, rfq_id, _, aircraft_id, departure, _ = _setup_rfq(client, suffix="QG")
        initial_body = _quote_body(aircraft_id, departure - timedelta(days=1))
        initial = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-revision-initial"},
            json=initial_body,
        )
        assert initial.status_code == 201
        first = initial.json()
        first_id = first["id"]

        revision_body = _quote_body(
            aircraft_id,
            departure - timedelta(hours=12),
            base_amount_minor=7_100_000,
        )
        revised = client.post(
            f"/v1/quotes/{first_id}/revise",
            headers={"Idempotency-Key": "pr8-revise-qg"},
            json=revision_body,
        )
        assert revised.status_code == 201
        second = revised.json()
        second_id = second["id"]
        assert second["revision_number"] == 2
        assert second["supersedes_quote_id"] == first_id
        assert second["base_price"]["amount_minor"] == 7_100_000

        replay = client.post(
            f"/v1/quotes/{first_id}/revise",
            headers={"Idempotency-Key": "pr8-revise-qg"},
            json=revision_body,
        )
        assert replay.status_code == 201
        assert replay.json() == second

        listed = client.get(f"/v1/rfqs/{rfq_id}/quotes")
        assert listed.status_code == 200
        history = listed.json()["quotes"]
        assert [item["revision_number"] for item in history] == [1, 2]
        assert history[0]["id"] == first_id
        assert history[0]["status"] == "superseded"
        assert history[0]["is_current"] is False
        assert history[0]["base_price"]["amount_minor"] == 7_400_000
        assert history[1]["id"] == second_id
        assert history[1]["status"] == "submitted"
        assert history[1]["is_current"] is True

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            first_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(first_id)},
                )
                .scalars()
                .all()
            )
            second_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(second_id)},
                )
                .scalars()
                .all()
            )
            assert first_events == ["QUOTE_SUBMITTED", "QUOTE_SUPERSEDED"]
            assert second_events == ["QUOTE_REVISED"]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_withdrawal_and_expiry_are_persisted_and_idempotent() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, rfq_id, _, aircraft_id, departure, _ = _setup_rfq(client, suffix="QH")
        submitted = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-withdraw-initial"},
            json=_quote_body(aircraft_id, departure - timedelta(days=1)),
        )
        assert submitted.status_code == 201
        quote_id = submitted.json()["id"]

        withdrawn = client.post(
            f"/v1/quotes/{quote_id}/withdraw",
            headers={"Idempotency-Key": "pr8-withdraw-qh"},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "withdrawn"
        assert withdrawn.json()["is_current"] is False
        replay = client.post(
            f"/v1/quotes/{quote_id}/withdraw",
            headers={"Idempotency-Key": "pr8-withdraw-qh"},
        )
        assert replay.status_code == 200
        assert replay.json() == withdrawn.json()
        duplicate = client.post(
            f"/v1/quotes/{quote_id}/withdraw",
            headers={"Idempotency-Key": "pr8-withdraw-qh-2"},
        )
        assert duplicate.status_code == 409

        _, exp_rfq_id, _, exp_aircraft, exp_departure, _ = _setup_rfq(
            client,
            suffix="QI",
        )
        exp_submitted = client.post(
            f"/v1/rfqs/{exp_rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-expire-initial"},
            json=_quote_body(exp_aircraft, exp_departure - timedelta(days=1)),
        )
        assert exp_submitted.status_code == 201
        exp_quote_id = exp_submitted.json()["id"]

        too_early = client.post(
            f"/v1/quotes/{exp_quote_id}/expire",
            headers={"Idempotency-Key": "pr8-expire-early"},
        )
        assert too_early.status_code == 409

        engine = create_engine(settings.database_url)
        try:
            now = datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE quotes SET submitted_at = :submitted, valid_until = :valid_until "
                        "WHERE id = :quote_id"
                    ),
                    {
                        "submitted": now - timedelta(hours=2),
                        "valid_until": now - timedelta(hours=1),
                        "quote_id": UUID(exp_quote_id),
                    },
                )
        finally:
            engine.dispose()

        expired = client.post(
            f"/v1/quotes/{exp_quote_id}/expire",
            headers={"Idempotency-Key": "pr8-expire-qi"},
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "expired"
        assert expired.json()["is_current"] is False
        expire_replay = client.post(
            f"/v1/quotes/{exp_quote_id}/expire",
            headers={"Idempotency-Key": "pr8-expire-qi"},
        )
        assert expire_replay.status_code == 200
        assert expire_replay.json() == expired.json()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_initial_submissions_allow_one_authoritative_quote() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, rfq_id, _, aircraft_id, departure, _ = _setup_rfq(client, suffix="QJ")
        body = _quote_body(aircraft_id, departure - timedelta(days=1))
        barrier = Barrier(2)

        def submit(key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/rfqs/{rfq_id}/quotes",
                headers={"Idempotency-Key": key},
                json=body,
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(submit, "pr8-race-submit-1")
            second = executor.submit(submit, "pr8-race-submit-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [201, 409]
        listed = client.get(f"/v1/rfqs/{rfq_id}/quotes")
        assert listed.status_code == 200
        assert len(listed.json()["quotes"]) == 1


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_revisions_and_terminal_transitions_are_single_use() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        _, rfq_id, _, aircraft_id, departure, _ = _setup_rfq(client, suffix="QK")
        initial = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": "pr8-race-revision-initial"},
            json=_quote_body(aircraft_id, departure - timedelta(days=1)),
        )
        assert initial.status_code == 201
        first_id = initial.json()["id"]
        barrier = Barrier(2)

        def revise(key: str, amount: int) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/quotes/{first_id}/revise",
                headers={"Idempotency-Key": key},
                json=_quote_body(
                    aircraft_id,
                    departure - timedelta(hours=12),
                    base_amount_minor=amount,
                ),
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(revise, "pr8-race-revise-1", 7_200_000)
            second = executor.submit(revise, "pr8-race-revise-2", 7_100_000)
            statuses = sorted((first.result(), second.result()))
        assert statuses == [201, 409]

        history = client.get(f"/v1/rfqs/{rfq_id}/quotes").json()["quotes"]
        assert len(history) == 2
        current = next(item for item in history if item["is_current"])
        current_id = current["id"]
        terminal_barrier = Barrier(2)

        def withdraw(key: str) -> int:
            terminal_barrier.wait()
            response = client.post(
                f"/v1/quotes/{current_id}/withdraw",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_terminal = executor.submit(withdraw, "pr8-race-withdraw-1")
            second_terminal = executor.submit(withdraw, "pr8-race-withdraw-2")
            terminal_statuses = sorted((first_terminal.result(), second_terminal.result()))
        assert terminal_statuses == [200, 409]

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            current_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(current_id)},
                )
                .scalars()
                .all()
            )
            assert current_events.count("QUOTE_WITHDRAWN") == 1
    finally:
        engine.dispose()
