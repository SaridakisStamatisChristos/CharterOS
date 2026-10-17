import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.capacity_support import seed_capacity_reference_profile


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _organization(client: TestClient, *, suffix: str, kind: str) -> str:
    response = client.post(
        "/v1/organizations",
        headers={"Idempotency-Key": f"pr21-org-{suffix}"},
        json={
            "type": kind,
            "legal_name": f"PR21 {kind.title()} {suffix}",
            "country": "GR",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _operator(client: TestClient, *, suffix: str) -> str:
    organization_id = _organization(client, suffix=f"OP-{suffix}", kind="operator")
    response = client.post(
        "/v1/operators",
        headers={"Idempotency-Key": f"pr21-operator-{suffix}"},
        json={
            "organization_id": organization_id,
            "aoc_reference": f"GR-PR21-{suffix}",
            "operating_regions": ["EU"],
            "verification_status": "verified",
            "insurance_status": "valid",
            "commercial_status": "active",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _airport(client: TestClient, *, suffix: str, ordinal: int) -> str:
    suffix_letter = next(char for char in suffix.upper() if char.isalpha())
    ordinal_letter = chr(ord("A") + ordinal - 1)
    icao = f"Z{suffix_letter}{ordinal_letter}Q"
    iata = f"{suffix_letter}{ordinal_letter}Q"
    response = client.post(
        "/v1/airports",
        headers={"Idempotency-Key": f"pr21-airport-{suffix}-{ordinal}"},
        json={
            "icao": icao,
            "iata": iata,
            "lat": str(37 + ordinal / 10),
            "lon": str(23 + ordinal / 10),
            "timezone": "Europe/Athens",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _portal_aircraft(
    client: TestClient,
    *,
    operator_id: str,
    home_base: str,
    suffix: str,
) -> str:
    body = {
        "registration": f"SX-{suffix}",
        "aircraft_type": {
            "manufacturer": "PR21 Airframes",
            "model": f"PortalJet {suffix}",
            "category": "regional",
            "seats_min": 1,
            "seats_max": 80,
            "range_nm": 3000,
            "runway_requirements": {},
            "baggage_cargo_profile": {},
        },
        "seat_capacity": 60,
        "cargo_capacity": "700",
        "range_nm": 2500,
        "home_base": home_base,
        "status": "active",
    }
    response = client.post(
        "/v1/operator-portal/fleet",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr21-fleet-{suffix}",
        },
        json=body,
    )
    assert response.status_code == 201
    replay = client.post(
        "/v1/operator-portal/fleet",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr21-fleet-{suffix}",
        },
        json=body,
    )
    assert replay.status_code == 201
    assert replay.json() == response.json()
    seed_capacity_reference_profile(
        response.json(),
        source=f"pr21-capacity-{suffix}",
    )
    return str(response.json()["id"])


def _mission_and_rfq(
    client: TestClient,
    *,
    suffix: str,
    buyer_id: str,
    operator_id: str,
    origin_id: str,
    destination_id: str,
    departure: datetime,
) -> tuple[str, str]:
    mission = client.post(
        "/v1/missions",
        headers={"Idempotency-Key": f"pr21-mission-{suffix}"},
        json={
            "buyer_id": buyer_id,
            "origin_airport_id": origin_id,
            "destination_airport_id": destination_id,
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 18,
            "special_requirements": ["wifi"],
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/missions/{mission_id}/open",
        headers={"Idempotency-Key": f"pr21-open-{suffix}"},
    )
    assert opened.status_code == 200
    rfq = client.post(
        f"/v1/missions/{mission_id}/rfqs",
        headers={"Idempotency-Key": f"pr21-rfq-{suffix}"},
        json={
            "operator_id": operator_id,
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfq.status_code == 201
    return mission_id, str(rfq.json()["id"])


def _quote_body(aircraft_id: str, valid_until: datetime, amount: int) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": "EUR",
        "base_amount_minor": amount,
        "repositioning_amount_minor": 100_000,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 50_000,
            }
        ],
        "inclusions": ["catering"],
        "exclusions": [],
        "valid_until": valid_until.isoformat(),
    }


@pytest.mark.integration
def test_pr21_operator_portal_isolation_workflows_and_calendar() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id = _organization(client, suffix="BUY-A", kind="buyer")
        origin_id = _airport(client, suffix="A1", ordinal=1)
        destination_id = _airport(client, suffix="A2", ordinal=2)
        operator_a = _operator(client, suffix="A")
        operator_b = _operator(client, suffix="B")
        aircraft_a = _portal_aircraft(
            client,
            operator_id=operator_a,
            home_base=origin_id,
            suffix="P21A",
        )
        aircraft_b = _portal_aircraft(
            client,
            operator_id=operator_b,
            home_base=origin_id,
            suffix="P21B",
        )

        fleet_a = client.get(
            "/v1/operator-portal/fleet?limit=1",
            headers={"X-Operator-Id": operator_a},
        )
        assert fleet_a.status_code == 200
        assert fleet_a.json()["returned_count"] == 1
        assert {item["operator_id"] for item in fleet_a.json()["aircraft"]} == {operator_a}

        cross_fleet = client.get(
            f"/v1/operator-portal/fleet/{aircraft_b}",
            headers={"X-Operator-Id": operator_a},
        )
        assert cross_fleet.status_code == 404

        availability_body = {
            "valid_from": (datetime.now(UTC) + timedelta(days=19)).isoformat(),
            "valid_to": (datetime.now(UTC) + timedelta(days=22)).isoformat(),
            "status": "available",
            "source": "operator-portal",
            "reason": "planned availability",
            "provenance": {"source": "pr21-test"},
        }
        forbidden_availability = client.post(
            f"/v1/operator-portal/fleet/{aircraft_b}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-cross-availability-a",
            },
            json=availability_body,
        )
        assert forbidden_availability.status_code == 404

        availability = client.post(
            f"/v1/operator-portal/fleet/{aircraft_a}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-availability-a",
            },
            json=availability_body,
        )
        assert availability.status_code == 201
        replay = client.post(
            f"/v1/operator-portal/fleet/{aircraft_a}/availability",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-availability-a",
            },
            json=availability_body,
        )
        assert replay.status_code == 201
        assert replay.json() == availability.json()

        position = client.post(
            f"/v1/aircraft/{aircraft_a}/positions",
            headers={"Idempotency-Key": "pr21-position-a"},
            json={
                "airport_id": origin_id,
                "event_time": datetime.now(UTC).isoformat(),
                "source": "operator-portal",
                "provenance": {"source": "pr21-test"},
            },
        )
        assert position.status_code == 201

        departure = datetime.now(UTC) + timedelta(days=20)
        mission_id, rfq_id = _mission_and_rfq(
            client,
            suffix="GEN-A",
            buyer_id=buyer_id,
            operator_id=operator_a,
            origin_id=origin_id,
            destination_id=destination_id,
            departure=departure,
        )

        inbox_a = client.get(
            "/v1/operator-portal/rfqs",
            headers={"X-Operator-Id": operator_a},
        )
        assert inbox_a.status_code == 200
        assert {item["operator_id"] for item in inbox_a.json()["rfqs"]} == {operator_a}
        assert rfq_id in {item["id"] for item in inbox_a.json()["rfqs"]}

        cross_rfq = client.get(
            f"/v1/operator-portal/rfqs/{rfq_id}",
            headers={"X-Operator-Id": operator_b},
        )
        assert cross_rfq.status_code == 404

        acknowledged = client.post(
            f"/v1/operator-portal/rfqs/{rfq_id}/acknowledge",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-ack-a",
            },
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "acknowledged"

        quote_body = _quote_body(
            aircraft_a,
            departure - timedelta(days=1),
            4_000_000,
        )
        submitted = client.post(
            f"/v1/operator-portal/rfqs/{rfq_id}/quotes",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-quote-a",
            },
            json=quote_body,
        )
        assert submitted.status_code == 201
        quote_id = str(submitted.json()["id"])
        assert submitted.json()["currency"] == "EUR"

        cross_quote = client.get(
            f"/v1/operator-portal/quotes/{quote_id}",
            headers={"X-Operator-Id": operator_b},
        )
        assert cross_quote.status_code == 404

        revised_body = _quote_body(
            aircraft_a,
            departure - timedelta(hours=12),
            3_900_000,
        )
        revised = client.post(
            f"/v1/operator-portal/quotes/{quote_id}/revise",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-revise-a",
            },
            json=revised_body,
        )
        assert revised.status_code == 201
        revised_id = str(revised.json()["id"])
        assert revised.json()["supersedes_quote_id"] == quote_id
        assert revised.json()["revision_number"] == 2
        assert revised.json()["currency"] == "EUR"

        awarded = client.post(
            f"/v1/quotes/{revised_id}/accept",
            headers={"Idempotency-Key": "pr21-award-a"},
        )
        assert awarded.status_code == 201
        booking_id = str(awarded.json()["id"])
        assert awarded.json()["mission_id"] == mission_id

        own_booking = client.get(
            f"/v1/operator-portal/bookings/{booking_id}",
            headers={"X-Operator-Id": operator_a},
        )
        assert own_booking.status_code == 200
        assert own_booking.json()["quote_currency"] == "EUR"

        cross_booking = client.get(
            f"/v1/operator-portal/bookings/{booking_id}",
            headers={"X-Operator-Id": operator_b},
        )
        assert cross_booking.status_code == 404

        calendar = client.get(
            "/v1/operator-portal/calendar",
            headers={"X-Operator-Id": operator_a},
            params={
                "window_start": (departure - timedelta(days=1)).isoformat(),
                "window_end": (departure + timedelta(days=1)).isoformat(),
            },
        )
        assert calendar.status_code == 200
        assert [item["id"] for item in calendar.json()["entries"]] == [booking_id]
        assert (
            datetime.fromisoformat(
                calendar.json()["entries"][0]["departure_from"].replace("Z", "+00:00")
            )
            == departure
        )

        no_portal_booking_transition = client.post(
            f"/v1/operator-portal/bookings/{booking_id}/confirm",
            headers={
                "X-Operator-Id": operator_a,
                "Idempotency-Key": "pr21-no-booking-transition",
            },
        )
        assert no_portal_booking_transition.status_code == 404


@pytest.mark.integration
def test_pr21_sealed_tender_requires_matching_invitation_capability() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        buyer_id = _organization(client, suffix="BUY-T", kind="buyer")
        origin_id = _airport(client, suffix="T1", ordinal=3)
        destination_id = _airport(client, suffix="T2", ordinal=4)
        operator_a = _operator(client, suffix="TA")
        operator_b = _operator(client, suffix="TB")
        aircraft_a = _portal_aircraft(
            client,
            operator_id=operator_a,
            home_base=origin_id,
            suffix="P21C",
        )
        _portal_aircraft(
            client,
            operator_id=operator_b,
            home_base=origin_id,
            suffix="P21D",
        )

        departure = datetime.now(UTC) + timedelta(days=12)
        mission = client.post(
            "/v1/missions",
            headers={"Idempotency-Key": "pr21-tender-mission"},
            json={
                "buyer_id": buyer_id,
                "origin_airport_id": origin_id,
                "destination_airport_id": destination_id,
                "departure_window": {
                    "start": departure.isoformat(),
                    "end": (departure + timedelta(hours=2)).isoformat(),
                },
                "passenger_count": 12,
            },
        )
        assert mission.status_code == 201
        mission_id = str(mission.json()["id"])
        assert (
            client.post(
                f"/v1/missions/{mission_id}/open",
                headers={"Idempotency-Key": "pr21-tender-open"},
            ).status_code
            == 200
        )
        tender = client.post(
            f"/v1/missions/{mission_id}/tenders",
            headers={"Idempotency-Key": "pr21-tender-create"},
            json={
                "opens_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                "deadline_at": (departure - timedelta(days=3)).isoformat(),
                "sealed_bid": True,
            },
        )
        assert tender.status_code == 201
        tender_id = str(tender.json()["id"])

        invitations: dict[str, dict[str, str]] = {}
        for label, operator_id in (("a", operator_a), ("b", operator_b)):
            response = client.post(
                f"/v1/tenders/{tender_id}/invitations",
                headers={"Idempotency-Key": f"pr21-tender-invite-{label}"},
                json={"operator_id": operator_id},
            )
            assert response.status_code == 201
            invitations[label] = {
                "id": str(response.json()["id"]),
                "rfq_id": str(response.json()["rfq_id"]),
            }

        rfq_a = invitations["a"]["rfq_id"]
        inbox = client.get(
            "/v1/operator-portal/rfqs",
            headers={"X-Operator-Id": operator_a},
        )
        assert inbox.status_code == 200
        payload = inbox.text
        assert rfq_a in payload
        assert operator_b not in payload
        assert invitations["b"]["rfq_id"] not in payload
        tender_item = next(item for item in inbox.json()["rfqs"] if item["id"] == rfq_a)
        assert tender_item["tender_capability_required"] is True
        assert "tender_invitation_id" not in tender_item

        missing_capability = client.get(
            f"/v1/operator-portal/rfqs/{rfq_a}",
            headers={"X-Operator-Id": operator_a},
        )
        assert missing_capability.status_code == 404

        wrong_capability = client.get(
            f"/v1/operator-portal/rfqs/{rfq_a}",
            headers={
                "X-Operator-Id": operator_a,
                "X-Tender-Invitation-Id": invitations["b"]["id"],
            },
        )
        assert wrong_capability.status_code == 404

        own_headers = {
            "X-Operator-Id": operator_a,
            "X-Tender-Invitation-Id": invitations["a"]["id"],
        }
        detail = client.get(
            f"/v1/operator-portal/rfqs/{rfq_a}",
            headers=own_headers,
        )
        assert detail.status_code == 200

        acknowledged = client.post(
            f"/v1/operator-portal/rfqs/{rfq_a}/acknowledge",
            headers={
                **own_headers,
                "Idempotency-Key": "pr21-tender-ack-a",
            },
        )
        assert acknowledged.status_code == 200

        bid = client.post(
            f"/v1/operator-portal/rfqs/{rfq_a}/quotes",
            headers={
                **own_headers,
                "Idempotency-Key": "pr21-tender-bid-a",
            },
            json=_quote_body(
                aircraft_a,
                departure - timedelta(days=2),
                5_000_000,
            ),
        )
        assert bid.status_code == 201
        quote_id = str(bid.json()["id"])

        own_quote = client.get(
            f"/v1/operator-portal/quotes/{quote_id}",
            headers=own_headers,
        )
        assert own_quote.status_code == 200

        competitor_attempt = client.get(
            f"/v1/operator-portal/quotes/{quote_id}",
            headers={
                "X-Operator-Id": operator_b,
                "X-Tender-Invitation-Id": invitations["b"]["id"],
            },
        )
        assert competitor_attempt.status_code == 404

        assert client.get(f"/v1/quotes/{quote_id}").status_code == 409
