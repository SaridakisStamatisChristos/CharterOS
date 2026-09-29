import os
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


def _operator_aircraft(
    client: TestClient,
    *,
    suffix: str,
    ordinal: int,
    home_base: str,
) -> tuple[str, str]:
    organization = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr17-op-org-{suffix}-{ordinal}"},
        json={
            "type": "operator",
            "legal_name": f"PR17 Operator {suffix} {ordinal}",
            "country": "GR",
        },
    )
    assert organization.status_code == 201
    operator = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr17-op-{suffix}-{ordinal}"},
        json={
            "organization_id": organization.json()["id"],
            "aoc_reference": f"GR-PR17-{suffix}-{ordinal}",
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
        headers={"Idempotency-Key": f"pr17-aircraft-{suffix}-{ordinal}"},
        json={
            "operator_id": operator_id,
            "registration": f"SX-T{suffix}{ordinal}",
            "aircraft_type": {
                "manufacturer": "PR17 Airframes",
                "model": f"TenderJet {suffix}-{ordinal}",
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
    return operator_id, str(aircraft.json()["id"])


def _quote_body(aircraft_id: str, valid_until: datetime, amount_minor: int) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount_minor,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 100_000,
            }
        ],
        "inclusions": ["Standard catering"],
        "valid_until": valid_until.isoformat(),
    }


def _setup_tender(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, str, datetime, list[dict[str, str]]]:
    buyer = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr17-buyer-{suffix}"},
        json={"type": "buyer", "legal_name": f"PR17 Buyer {suffix}", "country": "GR"},
    )
    assert buyer.status_code == 201
    origin = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr17-origin-{suffix}"},
        json={
            "icao": f"T{suffix}A",
            "iata": f"{suffix}A",
            "lat": "37.9364",
            "lon": "23.9445",
            "timezone": "Europe/Athens",
        },
    )
    destination = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr17-destination-{suffix}"},
        json={
            "icao": f"T{suffix}B",
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
        headers={"Idempotency-Key": f"pr17-mission-{suffix}"},
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
        headers={"Idempotency-Key": f"pr17-open-mission-{suffix}"},
    )
    assert opened.status_code == 200

    tender = client.post(
        f"/v1/missions/{mission_id}/tenders",
        headers={"Idempotency-Key": f"pr17-tender-{suffix}"},
        json={
            "opens_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "deadline_at": (departure - timedelta(days=2)).isoformat(),
            "sealed_bid": True,
        },
    )
    assert tender.status_code == 201
    tender_id = str(tender.json()["id"])
    assert tender.json()["status"] == "open"

    suppliers: list[dict[str, str]] = []
    for ordinal, amount in ((1, 7_000_000), (2, 8_000_000)):
        operator_id, aircraft_id = _operator_aircraft(
            client,
            suffix=suffix,
            ordinal=ordinal,
            home_base=str(origin.json()["id"]),
        )
        headers = {"Idempotency-Key": f"pr17-invite-{suffix}-{ordinal}"}
        invited = client.post(
            f"/v1/tenders/{tender_id}/invitations",
            headers=headers,
            json={"operator_id": operator_id},
        )
        assert invited.status_code == 201
        replay_invite = client.post(
            f"/v1/tenders/{tender_id}/invitations",
            headers=headers,
            json={"operator_id": operator_id},
        )
        assert replay_invite.status_code == 201
        assert replay_invite.json() == invited.json()
        invitation_id = str(invited.json()["id"])
        rfq_id = str(invited.json()["rfq_id"])

        accepted = client.post(
            f"/v1/tender-invitations/{invitation_id}/accept",
            headers={"Idempotency-Key": f"pr17-accept-invite-{suffix}-{ordinal}"},
        )
        assert accepted.status_code == 200

        bid_headers = {"Idempotency-Key": f"pr17-bid-{suffix}-{ordinal}"}
        bid_body = _quote_body(aircraft_id, departure - timedelta(days=1), amount)
        bid = client.post(
            f"/v1/tender-invitations/{invitation_id}/bids",
            headers=bid_headers,
            json=bid_body,
        )
        assert bid.status_code == 201
        replay_bid = client.post(
            f"/v1/tender-invitations/{invitation_id}/bids",
            headers=bid_headers,
            json=bid_body,
        )
        assert replay_bid.status_code == 201
        assert replay_bid.json() == bid.json()
        suppliers.append(
            {
                "operator_id": operator_id,
                "aircraft_id": aircraft_id,
                "invitation_id": invitation_id,
                "rfq_id": rfq_id,
                "quote_id": str(bid.json()["id"]),
                "amount": str(amount),
            }
        )
    return mission_id, tender_id, departure, suppliers


def _force_deadline_past(settings: Settings, tender_id: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        now = datetime.now(UTC)
        deadline = now - timedelta(minutes=1)
        created = now - timedelta(hours=1)
        opened = now - timedelta(minutes=50)
        with engine.begin() as connection:
            rfq_ids = connection.execute(
                text("SELECT rfq_id FROM tender_invitations WHERE tender_id = :id"),
                {"id": UUID(tender_id)},
            ).scalars().all()
            connection.execute(
                text(
                    "UPDATE tenders SET created_at = :created, opens_at = :created, "
                    "opened_at = :opened, deadline_at = :deadline WHERE id = :id"
                ),
                {
                    "created": created,
                    "opened": opened,
                    "deadline": deadline,
                    "id": UUID(tender_id),
                },
            )
            for rfq_id in rfq_ids:
                connection.execute(
                    text("UPDATE rfqs SET response_deadline = :deadline WHERE id = :id"),
                    {"deadline": deadline, "id": rfq_id},
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_sealed_tender_bafo_deadline_admin_correction_and_canonical_award() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, tender_id, departure, suppliers = _setup_tender(client, suffix="TA")
        first, second = suppliers

        supplier_view = client.get(
            f"/v1/tenders/{tender_id}/supplier-view",
            headers={"X-Operator-Id": first["operator_id"]},
        )
        assert supplier_view.status_code == 200
        payload_text = supplier_view.text
        assert supplier_view.json()["invitation"]["operator_id"] == first["operator_id"]
        assert len(supplier_view.json()["own_quotes"]) == 1
        assert second["operator_id"] not in payload_text
        assert second["quote_id"] not in payload_text
        assert second["amount"] not in payload_text

        bypass_revision = client.post(
            f"/v1/quotes/{first['quote_id']}/revise",
            headers={"Idempotency-Key": "pr17-bypass-revision-ta"},
            json=_quote_body(first["aircraft_id"], departure - timedelta(hours=12), 6_900_000),
        )
        assert bypass_revision.status_code == 409
        bypass_award = client.post(
            f"/v1/quotes/{first['quote_id']}/accept",
            headers={"Idempotency-Key": "pr17-bypass-award-ta"},
        )
        assert bypass_award.status_code == 409

        bafo = client.post(
            f"/v1/tenders/{tender_id}/best-and-final",
            headers={"Idempotency-Key": "pr17-bafo-ta"},
        )
        assert bafo.status_code == 200
        assert bafo.json()["status"] == "best_and_final"

        ordinary_revision = client.post(
            f"/v1/tender-invitations/{first['invitation_id']}/bids/{first['quote_id']}/revise",
            headers={"Idempotency-Key": "pr17-ordinary-after-bafo-ta"},
            json=_quote_body(first["aircraft_id"], departure - timedelta(hours=12), 6_850_000),
        )
        assert ordinary_revision.status_code == 409

        bafo_quotes: list[str] = []
        for ordinal, supplier in enumerate(suppliers, start=1):
            final = client.post(
                f"/v1/tender-invitations/{supplier['invitation_id']}"
                f"/best-and-final/{supplier['quote_id']}",
                headers={"Idempotency-Key": f"pr17-final-ta-{ordinal}"},
                json=_quote_body(
                    supplier["aircraft_id"],
                    departure - timedelta(hours=12),
                    6_700_000 + ordinal * 100_000,
                ),
            )
            assert final.status_code == 201
            bafo_quotes.append(str(final.json()["id"]))

        pre_close_audit = client.get(f"/v1/tenders/{tender_id}/audit")
        assert pre_close_audit.status_code == 200
        causal_event = next(
            event["event_id"]
            for event in reversed(pre_close_audit.json()["events"])
            if event["event_type"] == "TENDER_BEST_AND_FINAL_SUBMITTED"
        )

        _force_deadline_past(settings, tender_id)

        late_revision = client.post(
            f"/v1/tender-invitations/{first['invitation_id']}"
            f"/best-and-final/{bafo_quotes[0]}",
            headers={"Idempotency-Key": "pr17-late-final-ta"},
            json=_quote_body(first["aircraft_id"], departure - timedelta(hours=6), 6_500_000),
        )
        assert late_revision.status_code == 409

        closed = client.post(
            f"/v1/tenders/{tender_id}/close",
            headers={"Idempotency-Key": "pr17-close-ta"},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

        correction_body = {
            "actor_id": str(UUID("00000000-0000-0000-0000-000000000017")),
            "target_type": "quote",
            "target_id": bafo_quotes[0],
            "field_name": "payment_terms",
            "original_value": "50% on confirmation",
            "replacement_value": "50% on contract",
            "reason": "Correct transcription error while preserving original supplier evidence",
            "causation_event_id": causal_event,
        }
        correction = client.post(
            f"/v1/tenders/{tender_id}/admin-corrections",
            headers={"Idempotency-Key": "pr17-correction-ta"},
            json=correction_body,
        )
        assert correction.status_code == 201
        assert correction.json()["original_value"] == "50% on confirmation"
        assert correction.json()["replacement_value"] == "50% on contract"

        awarded = client.post(
            f"/v1/tenders/{tender_id}/award",
            headers={"Idempotency-Key": "pr17-award-ta"},
            json={"quote_id": bafo_quotes[0]},
        )
        assert awarded.status_code == 200
        award_body = awarded.json()
        assert award_body["tender"]["status"] == "awarded"
        assert award_body["booking"]["mission_id"] == mission_id
        assert award_body["booking"]["accepted_quote_id"] == bafo_quotes[0]

        award_replay = client.post(
            f"/v1/tenders/{tender_id}/award",
            headers={"Idempotency-Key": "pr17-award-ta"},
            json={"quote_id": bafo_quotes[0]},
        )
        assert award_replay.status_code == 200
        assert award_replay.json() == award_body

        audit = client.get(f"/v1/tenders/{tender_id}/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()["events"]]
        assert "TENDER_ADMIN_CORRECTED" in event_types
        assert "TENDER_AWARDED" in event_types
        assert len(audit.json()["corrections"]) == 1

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM bookings WHERE mission_id = :id"),
                {"id": UUID(mission_id)},
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT count(*) FROM tender_admin_corrections WHERE tender_id = :id"),
                {"id": UUID(tender_id)},
            ).scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_deadline_close_race_and_concurrent_award_preserve_single_winner() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        mission_id, tender_id, departure, suppliers = _setup_tender(client, suffix="TB")
        _force_deadline_past(settings, tender_id)

        barrier = Barrier(2)

        def close_tender() -> int:
            with TestClient(create_app(settings)) as worker:
                barrier.wait()
                return worker.post(
                    f"/v1/tenders/{tender_id}/close",
                    headers={"Idempotency-Key": "pr17-race-close-tb"},
                ).status_code

        def late_revision() -> int:
            supplier = suppliers[0]
            with TestClient(create_app(settings)) as worker:
                barrier.wait()
                return worker.post(
                    f"/v1/tender-invitations/{supplier['invitation_id']}"
                    f"/bids/{supplier['quote_id']}/revise",
                    headers={"Idempotency-Key": "pr17-race-late-revision-tb"},
                    json=_quote_body(
                        supplier["aircraft_id"], departure - timedelta(hours=8), 6_600_000
                    ),
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            close_future = pool.submit(close_tender)
            late_future = pool.submit(late_revision)
            assert close_future.result() == 200
            assert late_future.result() == 409

        award_barrier = Barrier(2)

        def award(ordinal: int) -> int:
            with TestClient(create_app(settings)) as worker:
                award_barrier.wait()
                return worker.post(
                    f"/v1/tenders/{tender_id}/award",
                    headers={"Idempotency-Key": f"pr17-concurrent-award-tb-{ordinal}"},
                    json={"quote_id": suppliers[ordinal - 1]["quote_id"]},
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(award, 1)
            second = pool.submit(award, 2)
            statuses = sorted((first.result(), second.result()))
        assert statuses == [200, 409]

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM bookings WHERE mission_id = :id"),
                {"id": UUID(mission_id)},
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT status FROM tenders WHERE id = :id"),
                {"id": UUID(tender_id)},
            ).scalar_one() == "awarded"
    finally:
        engine.dispose()
