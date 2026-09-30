import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from charteros.application.bookings import BookingService
from charteros.domain.bookings import Booking
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.ids import CorrelationId
from charteros.domain.tenders import TenderId

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.capacity_support import seed_capacity_reference_profile


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _create_operator_and_aircraft(
    client: TestClient,
    *,
    suffix: str,
    ordinal: int,
    home_base: str,
    departure: datetime,
) -> tuple[str, str]:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr11-op-org-{suffix}-{ordinal}"},
        json={
            "type": "operator",
            "legal_name": f"PR11 Operator {suffix} {ordinal}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201

    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr11-op-{suffix}-{ordinal}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR11-{suffix}-{ordinal}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert operator.status_code == 201
    operator_id = str(operator.json()["id"])

    aircraft = client.post(
        "/v1/aircraft",
        headers={"Idempotency-Key": f"pr11-aircraft-{suffix}-{ordinal}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-{suffix}{ordinal}",
            "aircraft_type": {
                "manufacturer": "PR11 Airframes",
                "model": f"AwardJet {suffix}-{ordinal}",
                "category": "regional",
                "seats_min": 1,
                "seats_max": 80,
                "range_nm": 3000,
                "runway_requirements": {},
                "baggage_cargo_profile": {},
            },
            "seat_capacity": 60,
            "cargo_capacity": "800",
            "range_nm": 2500,
            "home_base": home_base,
            "status": "active",
        },
    )
    assert aircraft.status_code == 201
    seed_capacity_reference_profile(
        aircraft.json(),
        source=f"pr11-capacity-{suffix}-{ordinal}",
    )
    aircraft_id = str(aircraft.json()["id"])
    position = client.post(
        f"/v1/aircraft/{aircraft_id}/positions",
        headers={"Idempotency-Key": f"pr11-position-{suffix}-{ordinal}"},
        json={
            "airport_id": home_base,
            "event_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "source": "pr11-award-fixture",
            "provenance": {"fixture": "pr11"},
        },
    )
    assert position.status_code == 201
    availability = client.post(
        f"/v1/aircraft/{aircraft_id}/availability",
        headers={"Idempotency-Key": f"pr11-availability-{suffix}-{ordinal}"},
        json={
            "valid_from": (departure - timedelta(hours=3)).isoformat(),
            "valid_to": (departure + timedelta(hours=6)).isoformat(),
            "status": "available",
            "source": "pr11-award-fixture",
            "provenance": {"fixture": "pr11"},
        },
    )
    assert availability.status_code == 201
    return operator_id, aircraft_id


def _quote_body(
    aircraft_id: str,
    valid_until: datetime,
    amount_minor: int,
) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount_minor,
        "valid_until": valid_until.isoformat(),
    }


def _setup_two_quotes(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, tuple[str, str], tuple[str, str], datetime]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr11-buyer-{suffix}"},
        json={
            "type": "buyer",
            "legal_name": f"PR11 Buyer {suffix}",
            "country": "GR",
        },
    )
    assert buyer.status_code == 201

    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr11-origin-{suffix}"},
        json={
            "icao": f"P{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr11-destination-{suffix}"},
        json={
            "icao": f"P{suffix}B",
            "iata": f"{suffix}B",
            "lat": "40.5197",
            "lon": "22.9709",
            "timezone": "Europe/Athens",
        },
    )
    assert origin.status_code == destination.status_code == 201

    departure = datetime.now(UTC) + timedelta(days=7)
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr11-mission-{suffix}"},
        json={
            "buyer_id": buyer.json()["id"],
            "origin_airport_id": origin.json()["id"],
            "destination_airport_id": destination.json()["id"],
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 20,
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr11-open-{suffix}"},
    )
    assert opened.status_code == 200

    quote_ids: list[str] = []
    rfq_ids: list[str] = []
    for ordinal, amount in ((1, 7_000_000), (2, 8_000_000)):
        operator_id, aircraft_id = _create_operator_and_aircraft(
            client,
            suffix=suffix,
            ordinal=ordinal,
            home_base=str(origin.json()["id"]),
            departure=departure,
        )
        rfq = client.post(
            f"/v1/missions/{mission_id}/rfqs",
            headers={"Idempotency-Key": f"pr11-rfq-{suffix}-{ordinal}"},
            json={
                "operator_id": operator_id,
                "response_deadline": (departure - timedelta(days=2)).isoformat(),
            },
        )
        assert rfq.status_code == 201
        rfq_id = str(rfq.json()["id"])
        rfq_ids.append(rfq_id)

        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": f"pr11-ack-{suffix}-{ordinal}"},
        )
        assert acknowledged.status_code == 200

        quote = client.post(
            f"/v1/rfqs/{rfq_id}/quotes",
            headers={"Idempotency-Key": f"pr11-quote-{suffix}-{ordinal}"},
            json=_quote_body(
                aircraft_id,
                departure - timedelta(days=1),
                amount,
            ),
        )
        assert quote.status_code == 201
        quote_ids.append(str(quote.json()["id"]))

    return mission_id, (quote_ids[0], quote_ids[1]), (rfq_ids[0], rfq_ids[1]), departure


@pytest.mark.integration
def test_explicit_award_is_atomic_idempotent_and_can_choose_higher_priced_quote() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, (lower_quote_id, chosen_quote_id), _, departure = _setup_two_quotes(
            client,
            suffix="AA",
        )

        awarded = client.post(
            f"/v1/quotes/{chosen_quote_id}/accept",
            headers={"Idempotency-Key": "pr11-award-a1"},
        )
        assert awarded.status_code == 201
        booking = awarded.json()
        booking_id = booking["id"]
        assert booking["mission_id"] == mission_id
        assert booking["accepted_quote_id"] == chosen_quote_id
        assert booking["state"] == "pending_contract"
        assert booking["version"] == 1

        replay = client.post(
            f"/v1/quotes/{chosen_quote_id}/accept",
            headers={"Idempotency-Key": "pr11-award-a1"},
        )
        assert replay.status_code == 201
        assert replay.json() == booking

        second_award = client.post(
            f"/v1/quotes/{lower_quote_id}/accept",
            headers={"Idempotency-Key": "pr11-second-award-a1"},
        )
        assert second_award.status_code == 409

        fetched_booking = client.get(f"/v1/bookings/{booking_id}")
        assert fetched_booking.status_code == 200
        assert fetched_booking.json() == booking

        mission = client.get(f"/v1/missions/{mission_id}")
        assert mission.status_code == 200
        assert mission.json()["status"] == "selected"
        assert mission.json()["version"] == 5

        winner = client.get(f"/v1/quotes/{chosen_quote_id}")
        loser = client.get(f"/v1/quotes/{lower_quote_id}")
        assert winner.status_code == loser.status_code == 200
        assert winner.json()["status"] == "accepted"
        assert winner.json()["is_current"] is False
        assert winner.json()["accepted_at"] is not None
        assert winner.json()["rejected_at"] is None
        assert loser.json()["status"] == "rejected"
        assert loser.json()["is_current"] is False
        assert loser.json()["accepted_at"] is None
        assert loser.json()["rejected_at"] is not None

        revise_after_award = client.post(
            f"/v1/quotes/{chosen_quote_id}/revise",
            headers={"Idempotency-Key": "pr11-revise-after-award-a1"},
            json=_quote_body(
                winner.json()["aircraft_id"],
                departure - timedelta(hours=12),
                7_500_000,
            ),
        )
        assert revise_after_award.status_code == 409

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            winner_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(chosen_quote_id)},
                )
                .scalars()
                .all()
            )
            loser_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(lower_quote_id)},
                )
                .scalars()
                .all()
            )
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
            mission_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id = :id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(mission_id)},
                )
                .scalars()
                .all()
            )

            assert winner_events == ["QUOTE_SUBMITTED", "QUOTE_ACCEPTED"]
            assert loser_events == ["QUOTE_SUBMITTED", "QUOTE_REJECTED"]
            assert booking_events == ["BOOKING_CREATED"]
            assert mission_events == [
                "MISSION_CREATED",
                "MISSION_OPENED",
                "MISSION_SOURCING",
                "MISSION_QUOTED",
                "MISSION_SELECTED",
            ]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_expired_award_fails_without_partial_booking_or_state_transition() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, (expired_quote_id, other_quote_id), _, _ = _setup_two_quotes(
            client,
            suffix="AB",
        )

        engine = create_engine(settings.database_url)
        try:
            now = datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE quotes SET submitted_at = :submitted_at, "
                        "valid_until = :valid_until WHERE id = :quote_id"
                    ),
                    {
                        "submitted_at": now - timedelta(hours=2),
                        "valid_until": now - timedelta(hours=1),
                        "quote_id": UUID(expired_quote_id),
                    },
                )
        finally:
            engine.dispose()

        rejected = client.post(
            f"/v1/quotes/{expired_quote_id}/accept",
            headers={"Idempotency-Key": "pr11-expired-a2"},
        )
        assert rejected.status_code == 409

        mission = client.get(f"/v1/missions/{mission_id}")
        expired_quote = client.get(f"/v1/quotes/{expired_quote_id}")
        other_quote = client.get(f"/v1/quotes/{other_quote_id}")
        assert mission.json()["status"] == "sourcing"
        assert expired_quote.json()["status"] == "submitted"
        assert other_quote.json()["status"] == "submitted"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            award_events: int = connection.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE event_type IN ('QUOTE_ACCEPTED','QUOTE_REJECTED','BOOKING_CREATED') "
                    "AND (aggregate_id = :first OR aggregate_id = :second "
                    "OR aggregate_id = :mission_id)"
                ),
                {
                    "first": UUID(expired_quote_id),
                    "second": UUID(other_quote_id),
                    "mission_id": UUID(mission_id),
                },
            ).scalar_one()
            assert award_events == 0
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_concurrent_acceptance_of_two_quotes_produces_exactly_one_booking() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, quote_ids, _, _ = _setup_two_quotes(client, suffix="AC")
        barrier = Barrier(2)

        def accept(quote_id: str, key: str) -> int:
            barrier.wait()
            response = client.post(
                f"/v1/quotes/{quote_id}/accept",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(accept, quote_ids[0], "pr11-race-a3-1")
            second = executor.submit(accept, quote_ids[1], "pr11-race-a3-2")
            statuses = sorted((first.result(), second.result()))

        assert statuses == [201, 409]
        first_quote = client.get(f"/v1/quotes/{quote_ids[0]}").json()
        second_quote = client.get(f"/v1/quotes/{quote_ids[1]}").json()
        assert sorted((first_quote["status"], second_quote["status"])) == [
            "accepted",
            "rejected",
        ]
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "selected"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            accepted: int = connection.execute(
                text(
                    "SELECT count(*) FROM quotes q JOIN rfqs r ON r.id = q.rfq_id "
                    "WHERE r.mission_id = :mission_id AND q.status = 'accepted'"
                ),
                {"mission_id": UUID(mission_id)},
            ).scalar_one()
            rejected: int = connection.execute(
                text(
                    "SELECT count(*) FROM quotes q JOIN rfqs r ON r.id = q.rfq_id "
                    "WHERE r.mission_id = :mission_id AND q.status = 'rejected'"
                ),
                {"mission_id": UUID(mission_id)},
            ).scalar_one()
            assert accepted == rejected == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_failure_after_award_staging_rolls_back_every_authoritative_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    original = BookingService.accept_quote

    def fail_after_staging(
        service: BookingService,
        *,
        quote_id: QuoteId,
        now: datetime,
        correlation_id: CorrelationId,
        tender_id: TenderId | None = None,
    ) -> Booking:
        original(
            service,
            quote_id=quote_id,
            now=now,
            correlation_id=correlation_id,
            tender_id=tender_id,
        )
        raise SQLAlchemyError("synthetic pre-commit infrastructure failure")

    monkeypatch.setattr(BookingService, "accept_quote", fail_after_staging)

    with TestClient(create_app(settings)) as client:
        mission_id, (_, quote_id), _, _ = _setup_two_quotes(client, suffix="P43FA")
        response = client.post(
            f"/v1/quotes/{quote_id}/accept",
            headers={"Idempotency-Key": "pr43-fail-after-staging"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "temporarily_unavailable"}
        assert client.get(f"/v1/quotes/{quote_id}").json()["status"] == "submitted"
        assert client.get(f"/v1/missions/{mission_id}").json()["status"] == "sourcing"

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM aircraft_capacity_reservations "
                        "WHERE mission_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_evidence_snapshots "
                        "WHERE decision_type = 'award_commit' AND subject_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE event_type IN "
                        "('QUOTE_ACCEPTED','QUOTE_REJECTED','MISSION_SELECTED',"
                        "'BOOKING_CREATED','AIRCRAFT_CAPACITY_RESERVED') "
                        "AND recorded_at >= now() - interval '10 minutes'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM idempotency_records "
                        "WHERE scope = :scope AND key = :key"
                    ),
                    {
                        "scope": f"POST:/v1/quotes/{quote_id}/accept",
                        "key": "pr43-fail-after-staging",
                    },
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr43_lost_response_after_commit_replays_one_canonical_award() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, (_, quote_id), _, _ = _setup_two_quotes(client, suffix="P43LR")
        key = "pr43-lost-response"

        committed = client.post(
            f"/v1/quotes/{quote_id}/accept",
            headers={"Idempotency-Key": key},
        )
        assert committed.status_code == 201
        canonical = committed.json()

        # Treat the first HTTP body as if it never reached the caller. The retry must recover the
        # already committed response from the transactionally stored idempotency record.
        replay = client.post(
            f"/v1/quotes/{quote_id}/accept",
            headers={"Idempotency-Key": key},
        )
        assert replay.status_code == 201
        assert replay.json() == canonical

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            reservation_id = connection.execute(
                text(
                    "SELECT id FROM aircraft_capacity_reservations "
                    "WHERE mission_id = :mission_id"
                ),
                {"mission_id": UUID(mission_id)},
            ).scalar_one()
            booking_id = UUID(canonical["id"])
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM aircraft_capacity_reservations "
                        "WHERE mission_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_evidence_snapshots "
                        "WHERE decision_type = 'award_commit' AND subject_id = :mission_id"
                    ),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            for aggregate_id, event_type in (
                (UUID(quote_id), "QUOTE_ACCEPTED"),
                (UUID(mission_id), "MISSION_SELECTED"),
                (booking_id, "BOOKING_CREATED"),
                (reservation_id, "AIRCRAFT_CAPACITY_RESERVED"),
            ):
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM outbox_events "
                            "WHERE aggregate_id = :aggregate_id AND event_type = :event_type"
                        ),
                        {"aggregate_id": aggregate_id, "event_type": event_type},
                    ).scalar_one()
                    == 1
                )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM idempotency_records "
                        "WHERE scope = :scope AND key = :key"
                    ),
                    {
                        "scope": f"POST:/v1/quotes/{quote_id}/accept",
                        "key": key,
                    },
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr43_simultaneous_identical_award_requests_converge_on_one_result() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, (_, quote_id), _, _ = _setup_two_quotes(client, suffix="P43DU")
        barrier = Barrier(2)
        key = "pr43-concurrent-duplicate"

        def award() -> tuple[int, dict[str, object]]:
            barrier.wait()
            response = client.post(
                f"/v1/quotes/{quote_id}/accept",
                headers={"Idempotency-Key": key},
            )
            return int(response.status_code), response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(award)
            second = executor.submit(award)
            results = (first.result(), second.result())

        assert [item[0] for item in results] == [201, 201]
        assert results[0][1] == results[1][1]

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM bookings WHERE mission_id = :mission_id"),
                    {"mission_id": UUID(mission_id)},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM idempotency_records "
                        "WHERE scope = :scope AND key = :key"
                    ),
                    {
                        "scope": f"POST:/v1/quotes/{quote_id}/accept",
                        "key": key,
                    },
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
