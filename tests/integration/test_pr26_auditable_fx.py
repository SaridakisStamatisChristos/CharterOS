from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from apps.api.main import create_app
from charteros.domain.shared.currency import Currency
from charteros.infrastructure.db.models.fx import FxLockRow
from charteros.infrastructure.db.repositories.fx import SqlAlchemyFxRateRepository
from charteros.shared.config import Settings
from tests.integration.test_pr25_audit_evidence import (
    _airport,
    _insert_profile,
    _operator_and_aircraft,
    _organization,
    _record_operational_state,
)


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _quote_body(
    *,
    aircraft_id: str,
    currency: str,
    amount_minor: int,
    valid_until: datetime,
) -> dict[str, object]:
    return {
        "aircraft_id": aircraft_id,
        "currency": currency,
        "base_amount_minor": amount_minor,
        "repositioning_amount_minor": 100_000,
        "price_components": [
            {
                "category": "handling",
                "label": "Handling",
                "amount_minor": 50_000,
                "applicability": "known",
            }
        ],
        "inclusions": ["catering"],
        "exclusions": [],
        "cancellation_terms": "Standard cancellation terms",
        "payment_terms": "Net 7",
        "valid_until": valid_until.isoformat(),
    }


def _setup_cross_currency(
    client: TestClient,
    settings: Settings,
    *,
    suffix: str,
    origin_icao: str,
    origin_iata: str,
    destination_icao: str,
    destination_iata: str,
) -> dict[str, str]:
    buyer_id = _organization(client, suffix=f"FX-BUY-{suffix}", kind="buyer")
    origin = _airport(
        client,
        icao=origin_icao,
        iata=origin_iata,
        lat="37.9364",
        lon="23.9445",
    )
    destination = _airport(
        client,
        icao=destination_icao,
        iata=destination_iata,
        lat="40.5197",
        lon="22.9709",
    )
    operator_eur, aircraft_eur, type_eur = _operator_and_aircraft(
        client,
        suffix=f"{suffix}A",
        home_base=origin,
    )
    operator_usd, aircraft_usd, type_usd = _operator_and_aircraft(
        client,
        suffix=f"{suffix}B",
        home_base=origin,
    )
    _insert_profile(settings, type_eur, suffix=f"{suffix.lower()}a")
    _insert_profile(settings, type_usd, suffix=f"{suffix.lower()}b")

    departure = datetime.now(UTC) + timedelta(days=10)
    _record_operational_state(
        client,
        aircraft_id=aircraft_eur,
        airport_id=origin,
        departure=departure,
        suffix=f"{suffix.lower()}a",
    )
    _record_operational_state(
        client,
        aircraft_id=aircraft_usd,
        airport_id=origin,
        departure=departure,
        suffix=f"{suffix.lower()}b",
    )

    mission = client.post(
        "/v1/buyer-portal/missions",
        headers={
            "X-Buyer-Id": buyer_id,
            "Idempotency-Key": f"pr26-mission-{suffix}",
        },
        json={
            "origin_airport_id": origin,
            "destination_airport_id": destination,
            "departure_window": {
                "start": departure.isoformat(),
                "end": (departure + timedelta(hours=2)).isoformat(),
            },
            "passenger_count": 40,
            "max_budget": {
                "amount_minor": 15_000_000,
                "currency": "EUR",
            },
            "special_requirements": ["wifi"],
        },
    )
    assert mission.status_code == 201
    mission_id = str(mission.json()["id"])
    opened = client.post(
        f"/v1/buyer-portal/missions/{mission_id}/open",
        headers={
            "X-Buyer-Id": buyer_id,
            "Idempotency-Key": f"pr26-open-{suffix}",
        },
    )
    assert opened.status_code == 200

    rfqs = client.post(
        f"/v1/buyer-portal/missions/{mission_id}/rfqs",
        headers={
            "X-Buyer-Id": buyer_id,
            "Idempotency-Key": f"pr26-rfqs-{suffix}",
        },
        json={
            "operator_ids": [operator_eur, operator_usd],
            "response_deadline": (departure - timedelta(days=2)).isoformat(),
        },
    )
    assert rfqs.status_code == 201
    rfq_by_operator = {
        str(item["operator_id"]): str(item["id"]) for item in rfqs.json()["rfqs"]
    }

    for operator_id, rfq_id in rfq_by_operator.items():
        acknowledged = client.post(
            f"/v1/rfqs/{rfq_id}/acknowledge",
            headers={"Idempotency-Key": f"pr26-ack-{suffix}-{operator_id}"},
        )
        assert acknowledged.status_code == 200

    eur_quote = client.post(
        f"/v1/rfqs/{rfq_by_operator[operator_eur]}/quotes",
        headers={"Idempotency-Key": f"pr26-eur-quote-{suffix}"},
        json=_quote_body(
            aircraft_id=aircraft_eur,
            currency="EUR",
            amount_minor=7_000_000,
            valid_until=departure - timedelta(days=1),
        ),
    )
    assert eur_quote.status_code == 201

    usd_quote = client.post(
        f"/v1/rfqs/{rfq_by_operator[operator_usd]}/quotes",
        headers={"Idempotency-Key": f"pr26-usd-quote-{suffix}"},
        json=_quote_body(
            aircraft_id=aircraft_usd,
            currency="USD",
            amount_minor=8_000_000,
            valid_until=departure - timedelta(days=1),
        ),
    )
    assert usd_quote.status_code == 201

    return {
        "buyer_id": buyer_id,
        "mission_id": mission_id,
        "eur_quote_id": str(eur_quote.json()["id"]),
        "usd_quote_id": str(usd_quote.json()["id"]),
    }


def _create_rate(
    client: TestClient,
    *,
    suffix: str,
    rate: str = "0.8421",
) -> dict[str, object]:
    response = client.post(
        "/v1/fx/rates",
        headers={"Idempotency-Key": f"pr26-rate-{suffix}"},
        json={
            "source_currency": "USD",
            "target_currency": "EUR",
            "rate": rate,
            "source_minor_exponent": 2,
            "target_minor_exponent": 2,
            "fx_source": "ecb-test",
            "fx_source_version": f"ecb-{suffix}-v1",
            "fx_timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert response.status_code == 201
    return response.json()


def _fx_lock(
    client: TestClient,
    *,
    buyer_id: str,
    mission_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    response = client.post(
        f"/v1/buyer-portal/missions/{mission_id}/quotes/compare/fx-locks",
        headers={
            "X-Buyer-Id": buyer_id,
            "Idempotency-Key": idempotency_key,
        },
        json={"base_currency": "EUR", "fx_source": "ecb-test"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
def test_pr26_lock_uses_exact_seen_rate_and_correction_only_affects_new_locks() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _setup_cross_currency(
            client,
            settings,
            suffix="FA",
            origin_icao="FXAA",
            origin_iata="FXA",
            destination_icao="FXAB",
            destination_iata="FXB",
        )

        baseline = client.get(
            f"/v1/buyer-portal/missions/{setup['mission_id']}/quotes/compare",
            headers={"X-Buyer-Id": setup["buyer_id"]},
        )
        assert baseline.status_code == 200
        assert baseline.json()["global_rank_available"] is False
        assert baseline.json()["fx_lock_id"] is None

        missing = client.post(
            f"/v1/buyer-portal/missions/{setup['mission_id']}/quotes/compare/fx-locks",
            headers={
                "X-Buyer-Id": setup["buyer_id"],
                "Idempotency-Key": "pr26-missing-rate-fa",
            },
            json={"base_currency": "EUR", "fx_source": "ecb-test"},
        )
        assert missing.status_code == 409
        assert "missing explicit FX evidence" in missing.json()["detail"]

        rate_v1 = _create_rate(client, suffix="fa")
        lock_v1 = _fx_lock(
            client,
            buyer_id=setup["buyer_id"],
            mission_id=setup["mission_id"],
            idempotency_key="pr26-lock-fa-v1",
        )
        retry_v1 = _fx_lock(
            client,
            buyer_id=setup["buyer_id"],
            mission_id=setup["mission_id"],
            idempotency_key="pr26-lock-fa-v1",
        )
        assert retry_v1["fx_lock_id"] == lock_v1["fx_lock_id"]
        assert lock_v1["global_rank_available"] is True
        assert lock_v1["base_currency"] == "EUR"
        locked_at = datetime.fromisoformat(str(lock_v1["fx_locked_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(lock_v1["fx_expires_at"]).replace("Z", "+00:00"))
        assert (expires_at - locked_at).total_seconds() == 30

        usd_v1 = next(
            quote for quote in lock_v1["quotes"] if quote["quote_id"] == setup["usd_quote_id"]
        )
        eur_v1 = next(
            quote for quote in lock_v1["quotes"] if quote["quote_id"] == setup["eur_quote_id"]
        )
        assert usd_v1["fx"]["fx_rate"] == "0.8421"
        assert usd_v1["fx"]["fx_rate_id"] == rate_v1["id"]
        assert usd_v1["fx"]["base_currency"] == "EUR"
        assert eur_v1["fx"]["fx_rate"] == "1"
        assert eur_v1["fx"]["fx_rate_id"] is None
        assert eur_v1["fx"]["fx_source"] == "identity"

        corrected = client.post(
            f"/v1/fx/rates/{rate_v1['id']}/corrections",
            headers={"Idempotency-Key": "pr26-correct-fa"},
            json={"rate": "0.85", "fx_source_version": "ecb-fa-v2"},
        )
        assert corrected.status_code == 201
        assert corrected.json()["revision_number"] == 2
        assert corrected.json()["supersedes_rate_id"] == rate_v1["id"]

        lock_v2 = _fx_lock(
            client,
            buyer_id=setup["buyer_id"],
            mission_id=setup["mission_id"],
            idempotency_key="pr26-lock-fa-v2",
        )
        usd_v2 = next(
            quote for quote in lock_v2["quotes"] if quote["quote_id"] == setup["usd_quote_id"]
        )
        assert usd_v2["fx"]["fx_rate"] == "0.85"
        assert usd_v2["fx"]["fx_rate_id"] == corrected.json()["id"]

        approval = client.post(
            (
                f"/v1/buyer-portal/missions/{setup['mission_id']}/quotes/"
                f"{setup['usd_quote_id']}/approve"
            ),
            headers={
                "X-Buyer-Id": setup["buyer_id"],
                "Idempotency-Key": "pr26-approve-fa",
            },
            json={
                "fx_lock_id": lock_v1["fx_lock_id"],
                "note": "Approve the exact 30-second price I saw",
            },
        )
        assert approval.status_code == 201

        evidence = client.get(
            f"/v1/evidence/missions/{setup['mission_id']}",
            headers={"X-Buyer-Id": setup["buyer_id"]},
        )
        assert evidence.status_code == 200
        package = evidence.json()
        source_types = {item["source_type"] for item in package["sources"]}
        assert "fx_lock" in source_types
        assert "fx_rate" in source_types
        fx_rates = [
            item for item in package["sources"] if item["source_type"] == "fx_rate"
        ]
        assert {item["source_id"] for item in fx_rates} == {rate_v1["id"]}

        decision = next(
            item
            for item in package["decisions"]
            if item["decision_type"] == "quote_comparison"
        )
        committed_lock = decision["content"]["fx_lock"]
        assert committed_lock["lock_id"] == lock_v1["fx_lock_id"]
        committed_usd = next(
            item
            for item in committed_lock["quotes"]
            if item["quote_id"] == setup["usd_quote_id"]
        )
        assert committed_usd["fx_rate"] == "0.8421"
        assert committed_usd["fx_rate_id"] == rate_v1["id"]


@pytest.mark.integration
@pytest.mark.regression
def test_pr26_historical_rate_resolution_never_uses_later_correction() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        rate_v1 = _create_rate(client, suffix="fb")
        corrected = client.post(
            f"/v1/fx/rates/{rate_v1['id']}/corrections",
            headers={"Idempotency-Key": "pr26-correct-fb"},
            json={"rate": "0.9", "fx_source_version": "ecb-fb-v2"},
        )
        assert corrected.status_code == 201

    original_recorded = datetime.fromisoformat(
        str(rate_v1["recorded_at"]).replace("Z", "+00:00")
    )
    corrected_recorded = datetime.fromisoformat(
        str(corrected.json()["recorded_at"]).replace("Z", "+00:00")
    )
    midpoint = original_recorded + (corrected_recorded - original_recorded) / 2

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            repository = SqlAlchemyFxRateRepository(session)
            historical = repository.latest_for_pair(
                source_currency=Currency("USD"),
                target_currency=Currency("EUR"),
                fx_source="ecb-test",
                known_as_of=midpoint,
            )
            current = repository.latest_for_pair(
                source_currency=Currency("USD"),
                target_currency=Currency("EUR"),
                fx_source="ecb-test",
                known_as_of=corrected_recorded + timedelta(microseconds=1),
            )
            assert historical is not None
            assert current is not None
            assert str(historical.id) == str(rate_v1["id"])
            assert historical.rate_text == "0.8421"
            assert str(current.id) == str(corrected.json()["id"])
            assert current.rate_text == "0.9"
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr26_expired_lock_fails_instead_of_repricing_silently() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _setup_cross_currency(
            client,
            settings,
            suffix="FC",
            origin_icao="FXCA",
            origin_iata="FXC",
            destination_icao="FXCB",
            destination_iata="FXD",
        )
        _create_rate(client, suffix="fc")
        locked = _fx_lock(
            client,
            buyer_id=setup["buyer_id"],
            mission_id=setup["mission_id"],
            idempotency_key="pr26-lock-fc",
        )

        engine = create_engine(settings.database_url)
        try:
            past = datetime.now(UTC) - timedelta(minutes=2)
            with Session(engine) as session, session.begin():
                session.execute(
                    update(FxLockRow)
                    .where(FxLockRow.id == UUID(str(locked["fx_lock_id"])))
                    .values(
                        locked_at=past,
                        expires_at=past + timedelta(seconds=30),
                    )
                )
        finally:
            engine.dispose()

        approval = client.post(
            (
                f"/v1/buyer-portal/missions/{setup['mission_id']}/quotes/"
                f"{setup['usd_quote_id']}/approve"
            ),
            headers={
                "X-Buyer-Id": setup["buyer_id"],
                "Idempotency-Key": "pr26-expired-approve-fc",
            },
            json={"fx_lock_id": locked["fx_lock_id"]},
        )
        assert approval.status_code == 409
        assert "expired" in approval.json()["detail"]


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr26_concurrent_rate_corrections_cannot_fork_lineage() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        rate = _create_rate(client, suffix="fd")

    def correct(key: str, value: str) -> int:
        with TestClient(create_app(settings)) as worker:
            response = worker.post(
                f"/v1/fx/rates/{rate['id']}/corrections",
                headers={"Idempotency-Key": key},
                json={
                    "rate": value,
                    "fx_source_version": f"race-{key}",
                },
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(
            pool.map(
                lambda item: correct(*item),
                (("pr26-race-a", "0.86"), ("pr26-race-b", "0.87")),
            )
        )

    assert statuses == [201, 409]


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr26_one_fx_lock_cannot_back_two_concurrent_approvals() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _setup_cross_currency(
            client,
            settings,
            suffix="FE",
            origin_icao="FXEA",
            origin_iata="FXE",
            destination_icao="FXEB",
            destination_iata="FXF",
        )
        _create_rate(client, suffix="fe")
        locked = _fx_lock(
            client,
            buyer_id=setup["buyer_id"],
            mission_id=setup["mission_id"],
            idempotency_key="pr26-lock-fe",
        )

    def approve(key: str) -> int:
        with TestClient(create_app(settings)) as worker:
            response = worker.post(
                (
                    f"/v1/buyer-portal/missions/{setup['mission_id']}/quotes/"
                    f"{setup['usd_quote_id']}/approve"
                ),
                headers={
                    "X-Buyer-Id": setup["buyer_id"],
                    "Idempotency-Key": key,
                },
                json={"fx_lock_id": locked["fx_lock_id"]},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(approve, ("pr26-approve-fe-a", "pr26-approve-fe-b")))

    assert statuses == [201, 409]
