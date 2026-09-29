import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main import create_app
from charteros.shared.config import Settings
from tests.integration.test_pr12_contracts_api import _setup_booking
from tests.integration.test_pr13_booking_workflow_api import _create_accepted_contract
from tests.integration.test_pr23_disruptions import _booked_operation


def _settings() -> Settings:
    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        pytest.skip("CHARTEROS_DATABASE_URL is required")
    return Settings(environment="test", database_url=database_url, _env_file=None)


def _completed_booking(
    client: TestClient,
    *,
    suffix: str,
) -> tuple[str, str, str, str]:
    booking_id, mission_id, buyer_id, operator_id = _setup_booking(client, suffix=suffix)
    _create_accepted_contract(client, booking_id=booking_id, suffix=suffix.lower())
    for ordinal, command in enumerate(
        (
            "mark-contracted",
            "mark-payment-pending",
            "confirm",
            "enter-pre-operation",
            "start-operation",
            "complete",
        ),
        start=1,
    ):
        response = client.post(
            f"/v1/bookings/{booking_id}/{command}",
            headers={"Idempotency-Key": f"pr24-{command}-{suffix}-{ordinal}"},
        )
        assert response.status_code == 200
    assert client.get(f"/v1/bookings/{booking_id}").json()["state"] == "completed"
    return booking_id, mission_id, buyer_id, operator_id


def _open_reconciliation(
    client: TestClient,
    *,
    booking_id: str,
    operator_id: str,
    suffix: str,
) -> dict[str, object]:
    response = client.post(
        f"/v1/bookings/{booking_id}/reconciliation",
        headers={
            "X-Operator-Id": operator_id,
            "Idempotency-Key": f"pr24-open-{suffix}",
        },
    )
    assert response.status_code == 201
    return dict(response.json())


def _positive_invoice_body(
    *,
    total: int = 8_300_000,
    surcharge: int = 300_000,
    reference: str = "INV-PR24-001",
    reason: str | None = "Fuel and destination handling surcharge",
) -> dict[str, object]:
    return {
        "invoice_reference": reference,
        "currency": "EUR",
        "total_amount_minor": total,
        "line_items": [
            {
                "category": "charter_base",
                "label": "Booked charter",
                "amount_minor": 8_000_000,
            },
            {
                "category": "fuel_surcharge",
                "label": "Post-operation surcharge",
                "amount_minor": surcharge,
                "reason": "Operator final invoice evidence",
            },
        ],
        "surcharge_reason": reason,
    }


@pytest.mark.integration
def test_pr24_positive_variance_dispute_partial_approval_and_completion_are_auditable() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, buyer_id, operator_id = _completed_booking(client, suffix="JQ")
        booking_before = client.get(f"/v1/bookings/{booking_id}").json()
        quote_id = str(booking_before["accepted_quote_id"])

        direct = client.post(
            f"/v1/bookings/{booking_id}/reconcile",
            headers={"Idempotency-Key": "pr24-direct-reconcile-jq"},
        )
        assert direct.status_code == 409

        reconciliation = _open_reconciliation(
            client,
            booking_id=booking_id,
            operator_id=operator_id,
            suffix="jq",
        )
        reconciliation_id = str(reconciliation["id"])
        assert reconciliation["status"] == "open"
        assert reconciliation["accepted_quote_id"] == quote_id
        assert reconciliation["booked_amount_minor"] == 8_000_000
        assert reconciliation["booked_worst_case_amount_minor"] == 8_000_000
        assert reconciliation["currency"] == "EUR"

        replay = client.post(
            f"/v1/bookings/{booking_id}/reconciliation",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-open-jq",
            },
        )
        assert replay.status_code == 201
        assert replay.json() == reconciliation

        buyer_view = client.get(
            f"/v1/reconciliations/{reconciliation_id}",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert buyer_view.status_code == 200
        assert (
            client.get(
                f"/v1/reconciliations/{reconciliation_id}",
                headers={"X-Buyer-Id": str(uuid4())},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/reconciliations/{reconciliation_id}",
                headers={"X-Operator-Id": str(uuid4())},
            ).status_code
            == 404
        )

        fx = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-fx-jq",
            },
            json={
                **_positive_invoice_body(),
                "currency": "USD",
            },
        )
        assert fx.status_code == 409

        missing_reason = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-missing-reason-jq",
            },
            json=_positive_invoice_body(reason=None),
        )
        assert missing_reason.status_code == 422

        submitted = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-invoice-jq",
            },
            json=_positive_invoice_body(),
        )
        assert submitted.status_code == 201
        invoice = submitted.json()
        invoice_id = str(invoice["id"])
        assert invoice["revision_number"] == 1
        assert invoice["variance_minor"] == 300_000
        assert invoice["booked_amount_minor"] == 8_000_000
        assert invoice["total_amount_minor"] == 8_300_000

        invoice_replay = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-invoice-jq",
            },
            json=_positive_invoice_body(),
        )
        assert invoice_replay.status_code == 201
        assert invoice_replay.json() == invoice

        dispute = client.post(
            f"/v1/reconciliations/{reconciliation_id}/disputes",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-dispute-jq",
            },
            json={
                "disputed_amount_minor": 300_000,
                "reason": "Only half the additional handling is supported",
            },
        )
        assert dispute.status_code == 201
        dispute_id = str(dispute.json()["id"])
        assert dispute.json()["invoice_revision_id"] == invoice_id

        blocked = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-blocked-complete-jq",
            },
        )
        assert blocked.status_code == 409

        implicit_dispute_override = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-implicit-approval-jq",
            },
            json={"approved_variance_minor": 150_000},
        )
        assert implicit_dispute_override.status_code == 409

        approval = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-approval-jq",
            },
            json={
                "approved_variance_minor": 150_000,
                "resolves_dispute_id": dispute_id,
                "note": "Buyer approves negotiated half-variance settlement",
            },
        )
        assert approval.status_code == 201
        assert approval.json()["approved_variance_minor"] == 150_000
        assert approval.json()["resolves_dispute_id"] == dispute_id

        completed = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-complete-jq",
            },
        )
        assert completed.status_code == 200
        final = completed.json()
        assert final["status"] == "completed"
        assert final["final_invoice_revision_id"] == invoice_id
        assert final["approved_variance_minor"] == 150_000
        assert final["final_payable_minor"] == 8_150_000

        completion_replay = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-complete-jq",
            },
        )
        assert completion_replay.status_code == 200
        assert completion_replay.json() == final

        booking_after = client.get(f"/v1/bookings/{booking_id}")
        assert booking_after.status_code == 200
        assert booking_after.json()["state"] == "reconciled"
        assert booking_after.json()["version"] == 8
        assert booking_after.json()["accepted_quote_id"] == quote_id

        quote_after = client.get(f"/v1/quotes/{quote_id}")
        assert quote_after.status_code == 200
        assert quote_after.json()["status"] == "accepted"

        terminal_invoice = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-after-complete-jq",
            },
            json=_positive_invoice_body(),
        )
        assert terminal_invoice.status_code == 409

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            reconciliation_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id=:id ORDER BY aggregate_version"
                    ),
                    {"id": UUID(reconciliation_id)},
                )
                .scalars()
                .all()
            )
            assert reconciliation_events == [
                "FINANCIAL_RECONCILIATION_OPENED",
                "OPERATOR_INVOICE_SUBMITTED",
                "FINANCIAL_RECONCILIATION_DISPUTED",
                "FINANCIAL_VARIANCE_APPROVED",
                "FINANCIAL_RECONCILIATION_COMPLETED",
            ]
            booking_events: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE aggregate_id=:id AND event_type='BOOKING_RECONCILED'"
                    ),
                    {"id": UUID(booking_id)},
                )
                .scalars()
                .all()
            )
            assert booking_events == ["BOOKING_RECONCILED"]
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM operator_invoice_lines WHERE invoice_revision_id=:id"
                    ),
                    {"id": UUID(invoice_id)},
                ).scalar_one()
                == 2
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_pr24_invoice_revision_supersedes_dispute_and_stale_resolution_evidence_fails() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, buyer_id, operator_id = _completed_booking(client, suffix="JR")
        reconciliation = _open_reconciliation(
            client,
            booking_id=booking_id,
            operator_id=operator_id,
            suffix="jr",
        )
        reconciliation_id = str(reconciliation["id"])

        first = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-invoice-1-jr",
            },
            json=_positive_invoice_body(reference="INV-JR-1"),
        )
        assert first.status_code == 201
        first_id = str(first.json()["id"])

        dispute = client.post(
            f"/v1/reconciliations/{reconciliation_id}/disputes",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-dispute-jr",
            },
            json={
                "disputed_amount_minor": 300_000,
                "reason": "Request revised supporting evidence",
            },
        )
        assert dispute.status_code == 201
        dispute_id = str(dispute.json()["id"])

        revised = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-invoice-2-jr",
            },
            json=_positive_invoice_body(
                total=8_100_000,
                surcharge=100_000,
                reference="INV-JR-2",
                reason="Reduced supported handling surcharge",
            ),
        )
        assert revised.status_code == 201
        revised_body = revised.json()
        assert revised_body["revision_number"] == 2
        assert revised_body["supersedes_invoice_revision_id"] == first_id
        assert revised_body["variance_minor"] == 100_000

        stale_resolution = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-stale-dispute-jr",
            },
            json={
                "approved_variance_minor": 100_000,
                "resolves_dispute_id": dispute_id,
            },
        )
        assert stale_resolution.status_code == 409

        approval = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-approval-jr",
            },
            json={"approved_variance_minor": 100_000},
        )
        assert approval.status_code == 201

        completed = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-complete-jr",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["final_payable_minor"] == 8_100_000

        invoices = client.get(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={"X-Buyer-Id": buyer_id},
        )
        assert invoices.status_code == 200
        assert invoices.json()["returned_count"] == 2
        assert [item["status"] for item in invoices.json()["invoices"]] == [
            "superseded",
            "current",
        ]


@pytest.mark.integration
def test_pr24_negative_variance_completes_without_approval() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, buyer_id, operator_id = _completed_booking(client, suffix="JS")
        reconciliation = _open_reconciliation(
            client,
            booking_id=booking_id,
            operator_id=operator_id,
            suffix="js",
        )
        reconciliation_id = str(reconciliation["id"])

        invoice = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-credit-js",
            },
            json={
                "invoice_reference": "INV-JS-CREDIT",
                "currency": "EUR",
                "total_amount_minor": 7_900_000,
                "line_items": [
                    {
                        "category": "charter_base",
                        "label": "Booked charter",
                        "amount_minor": 8_000_000,
                    },
                    {
                        "category": "credit",
                        "label": "Service credit",
                        "amount_minor": -100_000,
                        "reason": "Post-operation service credit",
                    },
                ],
            },
        )
        assert invoice.status_code == 201
        assert invoice.json()["variance_minor"] == -100_000

        unnecessary_approval = client.post(
            f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-unnecessary-approval-js",
            },
            json={"approved_variance_minor": 0},
        )
        assert unnecessary_approval.status_code == 409

        completed = client.post(
            f"/v1/reconciliations/{reconciliation_id}/complete",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-complete-js",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["approved_variance_minor"] == 0
        assert completed.json()["final_payable_minor"] == 7_900_000


@pytest.mark.integration
@pytest.mark.concurrency
def test_pr24_competing_buyer_decisions_and_terminal_completion_serialize() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        booking_id, _, buyer_id, operator_id = _completed_booking(client, suffix="JT")
        reconciliation = _open_reconciliation(
            client,
            booking_id=booking_id,
            operator_id=operator_id,
            suffix="jt",
        )
        reconciliation_id = str(reconciliation["id"])
        invoice = client.post(
            f"/v1/reconciliations/{reconciliation_id}/invoices",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-race-invoice-jt",
            },
            json=_positive_invoice_body(reference="INV-JT-RACE"),
        )
        assert invoice.status_code == 201

        barrier = Barrier(2)

        def dispute() -> int:
            barrier.wait()
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/disputes",
                headers={
                    "X-Buyer-Id": buyer_id,
                    "Idempotency-Key": "pr24-race-dispute-jt",
                },
                json={
                    "disputed_amount_minor": 300_000,
                    "reason": "Concurrent dispute intent",
                },
            )
            return int(response.status_code)

        def approve() -> int:
            barrier.wait()
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
                headers={
                    "X-Buyer-Id": buyer_id,
                    "Idempotency-Key": "pr24-race-approve-jt",
                },
                json={"approved_variance_minor": 300_000},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            dispute_future = executor.submit(dispute)
            approve_future = executor.submit(approve)
            decision_statuses = sorted((dispute_future.result(), approve_future.result()))
        assert decision_statuses == [201, 409]

        state = client.get(
            f"/v1/reconciliations/{reconciliation_id}",
            headers={"X-Buyer-Id": buyer_id},
        ).json()
        if state["status"] == "disputed":
            resolved = client.post(
                f"/v1/reconciliations/{reconciliation_id}/variance-approvals",
                headers={
                    "X-Buyer-Id": buyer_id,
                    "Idempotency-Key": "pr24-resolve-race-dispute-jt",
                },
                json={
                    "approved_variance_minor": 300_000,
                    "resolves_dispute_id": state["current_dispute_id"],
                },
            )
            assert resolved.status_code == 201
        else:
            assert state["status"] == "variance_approved"

        complete_barrier = Barrier(2)

        def complete(key: str) -> int:
            complete_barrier.wait()
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/complete",
                headers={"X-Operator-Id": operator_id, "Idempotency-Key": key},
            )
            return int(response.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(complete, "pr24-race-complete-1-jt")
            second = executor.submit(complete, "pr24-race-complete-2-jt")
            completion_statuses = sorted((first.result(), second.result()))
        assert completion_statuses == [200, 409]

        final = client.get(f"/v1/bookings/{booking_id}")
        assert final.status_code == 200
        assert final.json()["state"] == "reconciled"


@pytest.mark.integration
def test_pr24_booked_baseline_includes_resolved_pr23_commercial_adjustment() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        setup = _booked_operation(client, suffix="DU")
        booking_id = str(setup["booking_id"])
        buyer_id = str(setup["buyer_id"])
        operator_id = str(setup["operator_id"])

        disruption = client.post(
            f"/v1/bookings/{booking_id}/disruptions",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-disruption-du",
            },
            json={
                "disruption_type": "other",
                "reason": "Post-booking operating-cost change",
            },
        )
        assert disruption.status_code == 201
        disruption_id = str(disruption.json()["id"])

        proposal = client.post(
            f"/v1/disruptions/{disruption_id}/replacement-options",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-proposal-du",
            },
            json={
                "source": "operations",
                "source_evidence": "Original operation retained",
            },
        )
        assert proposal.status_code == 201
        proposal_id = str(proposal.json()["id"])

        change = client.post(
            f"/v1/disruptions/{disruption_id}/requotes",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-change-du",
            },
            json={
                "proposal_id": proposal_id,
                "currency": "EUR",
                "known_adjustment_minor": 125_000,
                "conditional_adjustment_minor": 25_000,
                "terms_summary": "Buyer-approved post-booking commercial adjustment",
            },
        )
        assert change.status_code == 201
        change_id = str(change.json()["id"])

        approval = client.post(
            f"/v1/disruptions/{disruption_id}/buyer-decisions",
            headers={
                "X-Buyer-Id": buyer_id,
                "Idempotency-Key": "pr24-disruption-approval-du",
            },
            json={
                "proposal_id": proposal_id,
                "commercial_change_id": change_id,
                "decision": "approved",
            },
        )
        assert approval.status_code == 201

        resolved = client.post(
            f"/v1/disruptions/{disruption_id}/resolve",
            headers={
                "X-Operator-Id": operator_id,
                "Idempotency-Key": "pr24-disruption-resolve-du",
            },
            json={
                "proposal_id": proposal_id,
                "outcome": "Commercial adjustment accepted; operation retained",
            },
        )
        assert resolved.status_code == 200

        _create_accepted_contract(client, booking_id=booking_id, suffix="du")
        for ordinal, command in enumerate(
            (
                "mark-contracted",
                "mark-payment-pending",
                "confirm",
                "enter-pre-operation",
                "start-operation",
                "complete",
            ),
            start=1,
        ):
            response = client.post(
                f"/v1/bookings/{booking_id}/{command}",
                headers={"Idempotency-Key": f"pr24-disruption-baseline-{command}-{ordinal}"},
            )
            assert response.status_code == 200

        reconciliation = _open_reconciliation(
            client,
            booking_id=booking_id,
            operator_id=operator_id,
            suffix="du",
        )
        assert reconciliation["booked_amount_minor"] == 8_275_000
        assert reconciliation["booked_worst_case_amount_minor"] == 8_330_000

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            opening_payload = connection.execute(
                text(
                    "SELECT canonical_json FROM outbox_events "
                    "WHERE aggregate_id=:id "
                    "AND event_type='FINANCIAL_RECONCILIATION_OPENED'"
                ),
                {"id": UUID(str(reconciliation["id"]))},
            ).scalar_one()
            assert change_id in opening_payload
    finally:
        engine.dispose()
