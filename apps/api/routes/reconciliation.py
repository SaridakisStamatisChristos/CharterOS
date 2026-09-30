from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.dependencies import get_clock, get_correlation_id, get_session
from charteros.shared.clock import Clock
from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import (
    IdempotencyRepository,
    StoredResponse,
    canonical_request_hash,
)
from charteros.application.reconciliation import (
    FinancialReconciliationService,
    InvoiceLineInput,
    ReconciliationPartyContext,
)
from charteros.domain.bookings import BookingId
from charteros.domain.operators import OperatorId
from charteros.domain.organizations import OrganizationId
from charteros.domain.reconciliation import (
    FinancialReconciliation,
    FinancialReconciliationId,
    FinancialReconciliationStatus,
    InvoiceLineCategory,
    OperatorInvoiceRevision,
    ReconciliationDispute,
    ReconciliationDisputeId,
    VarianceApproval,
)
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.ids import CorrelationId
from charteros.infrastructure.db.repositories import (
    SqlAlchemyBookingRepository,
    SqlAlchemyDisruptionRepository,
    SqlAlchemyDomainEventRepository,
    SqlAlchemyFinancialReconciliationRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyQuoteRepository,
    SqlAlchemyRfqRepository,
)
from charteros.infrastructure.db.repositories.catalog import SqlAlchemyIdempotencyRepository

router = APIRouter(prefix="/v1", tags=["financial-reconciliation"])

SessionDep = Annotated[Session, Depends(get_session)]
CorrelationIdDep = Annotated[CorrelationId, Depends(get_correlation_id)]
OperatorIdDep = Annotated[UUID, Header(alias="X-Operator-Id")]
BuyerIdDep = Annotated[UUID, Header(alias="X-Buyer-Id")]
OperatorIdOptional = Annotated[UUID | None, Header(alias="X-Operator-Id")]
BuyerIdOptional = Annotated[UUID | None, Header(alias="X-Buyer-Id")]
IdempotencyKeyDep = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
InvoiceLimit = Annotated[int, Query(ge=1, le=100)]


class InvoiceLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: InvoiceLineCategory
    label: str = Field(min_length=1, max_length=200)
    amount_minor: int
    reason: str | None = Field(default=None, max_length=1000)


class OperatorInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_reference: str = Field(min_length=1, max_length=160)
    currency: str = Field(min_length=3, max_length=3)
    total_amount_minor: int = Field(gt=0)
    line_items: list[InvoiceLineRequest] = Field(min_length=1, max_length=256)
    surcharge_reason: str | None = Field(default=None, max_length=2000)


class DisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disputed_amount_minor: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


class VarianceApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_variance_minor: int = Field(ge=0)
    resolves_dispute_id: UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class FinancialReconciliationResponse(BaseModel):
    id: UUID
    version: int
    booking_id: UUID
    accepted_quote_id: UUID
    buyer_id: UUID
    operator_id: UUID
    currency: str
    quote_normalization_version: str
    quote_revision_number: int
    booked_amount_minor: int
    booked_worst_case_amount_minor: int
    opened_at: datetime
    status: FinancialReconciliationStatus
    current_invoice_revision_id: UUID | None
    current_dispute_id: UUID | None
    current_variance_approval_id: UUID | None
    final_invoice_revision_id: UUID | None
    approved_variance_minor: int | None
    final_payable_minor: int | None
    completed_at: datetime | None


class InvoiceLineResponse(BaseModel):
    line_number: int
    category: InvoiceLineCategory
    label: str
    amount_minor: int
    reason: str | None


class OperatorInvoiceResponse(BaseModel):
    id: UUID
    reconciliation_id: UUID
    revision_number: int
    supersedes_invoice_revision_id: UUID | None
    status: str
    invoice_reference: str
    currency: str
    booked_amount_minor: int
    line_items: list[InvoiceLineResponse]
    total_amount_minor: int
    variance_minor: int
    surcharge_reason: str | None
    submitted_at: datetime
    superseded_at: datetime | None


class OperatorInvoiceListResponse(BaseModel):
    reconciliation_id: UUID
    returned_count: int
    invoices: list[OperatorInvoiceResponse]


class DisputeResponse(BaseModel):
    id: UUID
    reconciliation_id: UUID
    invoice_revision_id: UUID
    buyer_id: UUID
    disputed_amount_minor: int
    currency: str
    reason: str
    opened_at: datetime


class VarianceApprovalResponse(BaseModel):
    id: UUID
    reconciliation_id: UUID
    invoice_revision_id: UUID
    buyer_id: UUID
    approved_variance_minor: int
    currency: str
    resolves_dispute_id: UUID | None
    approved_at: datetime
    note: str | None


def _service(session: Session) -> FinancialReconciliationService:
    return FinancialReconciliationService(
        reconciliations=SqlAlchemyFinancialReconciliationRepository(session),
        disruptions=SqlAlchemyDisruptionRepository(session),
        bookings=SqlAlchemyBookingRepository(session),
        missions=SqlAlchemyMissionRepository(session),
        quotes=SqlAlchemyQuoteRepository(session),
        rfqs=SqlAlchemyRfqRepository(session),
        organizations=SqlAlchemyOrganizationRepository(session),
        operators=SqlAlchemyOperatorRepository(session),
        events=SqlAlchemyDomainEventRepository(session),
    )


def _party(buyer_id: UUID | None, operator_id: UUID | None) -> ReconciliationPartyContext:
    return ReconciliationPartyContext(
        buyer_id=OrganizationId(buyer_id) if buyer_id is not None else None,
        operator_id=OperatorId(operator_id) if operator_id is not None else None,
    )


def _stored_response(
    repository: IdempotencyRepository,
    *,
    scope: str,
    key: str,
    request_hash: str,
) -> StoredResponse | None:
    stored = repository.get(scope, key)
    if stored is None:
        return None
    if stored.request_hash != request_hash:
        raise EntityConflictError("idempotency key was already used with a different request body")
    return stored


def _reconciliation_response(
    reconciliation: FinancialReconciliation,
) -> FinancialReconciliationResponse:
    return FinancialReconciliationResponse(
        id=reconciliation.id.value,
        version=reconciliation.version,
        booking_id=reconciliation.booking_id.value,
        accepted_quote_id=reconciliation.accepted_quote_id.value,
        buyer_id=reconciliation.buyer_id.value,
        operator_id=reconciliation.operator_id.value,
        currency=str(reconciliation.currency),
        quote_normalization_version=reconciliation.quote_normalization_version,
        quote_revision_number=reconciliation.quote_revision_number,
        booked_amount_minor=reconciliation.booked_amount.amount_minor,
        booked_worst_case_amount_minor=reconciliation.booked_worst_case_amount.amount_minor,
        opened_at=reconciliation.opened_at,
        status=reconciliation.status,
        current_invoice_revision_id=(
            reconciliation.current_invoice_revision_id.value
            if reconciliation.current_invoice_revision_id is not None
            else None
        ),
        current_dispute_id=(
            reconciliation.current_dispute_id.value
            if reconciliation.current_dispute_id is not None
            else None
        ),
        current_variance_approval_id=(
            reconciliation.current_variance_approval_id.value
            if reconciliation.current_variance_approval_id is not None
            else None
        ),
        final_invoice_revision_id=(
            reconciliation.final_invoice_revision_id.value
            if reconciliation.final_invoice_revision_id is not None
            else None
        ),
        approved_variance_minor=(
            reconciliation.approved_variance.amount_minor
            if reconciliation.approved_variance is not None
            else None
        ),
        final_payable_minor=(
            reconciliation.final_payable.amount_minor
            if reconciliation.final_payable is not None
            else None
        ),
        completed_at=reconciliation.completed_at,
    )


def _invoice_response(invoice: OperatorInvoiceRevision) -> OperatorInvoiceResponse:
    return OperatorInvoiceResponse(
        id=invoice.id.value,
        reconciliation_id=invoice.reconciliation_id.value,
        revision_number=invoice.revision_number,
        supersedes_invoice_revision_id=(
            invoice.supersedes_invoice_revision_id.value
            if invoice.supersedes_invoice_revision_id is not None
            else None
        ),
        status=invoice.status.value,
        invoice_reference=invoice.invoice_reference,
        currency=str(invoice.currency),
        booked_amount_minor=invoice.booked_amount.amount_minor,
        line_items=[
            InvoiceLineResponse(
                line_number=line.line_number,
                category=line.category,
                label=line.label,
                amount_minor=line.amount.amount_minor,
                reason=line.reason,
            )
            for line in invoice.line_items
        ],
        total_amount_minor=invoice.total_amount.amount_minor,
        variance_minor=invoice.variance.amount_minor,
        surcharge_reason=invoice.surcharge_reason,
        submitted_at=invoice.submitted_at,
        superseded_at=invoice.superseded_at,
    )


def _dispute_response(dispute: ReconciliationDispute) -> DisputeResponse:
    return DisputeResponse(
        id=dispute.id.value,
        reconciliation_id=dispute.reconciliation_id.value,
        invoice_revision_id=dispute.invoice_revision_id.value,
        buyer_id=dispute.buyer_id.value,
        disputed_amount_minor=dispute.disputed_amount.amount_minor,
        currency=str(dispute.disputed_amount.currency),
        reason=dispute.reason,
        opened_at=dispute.opened_at,
    )


def _approval_response(approval: VarianceApproval) -> VarianceApprovalResponse:
    return VarianceApprovalResponse(
        id=approval.id.value,
        reconciliation_id=approval.reconciliation_id.value,
        invoice_revision_id=approval.invoice_revision_id.value,
        buyer_id=approval.buyer_id.value,
        approved_variance_minor=approval.approved_variance.amount_minor,
        currency=str(approval.approved_variance.currency),
        resolves_dispute_id=(
            approval.resolves_dispute_id.value if approval.resolves_dispute_id is not None else None
        ),
        approved_at=approval.approved_at,
        note=approval.note,
    )


@router.post(
    "/bookings/{booking_id}/reconciliation",
    response_model=FinancialReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
)
def open_reconciliation(
    booking_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> FinancialReconciliationResponse:
    scope = f"POST:/v1/bookings/{booking_id}/reconciliation:operator:{operator_id}"
    request_hash = canonical_request_hash({})
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return FinancialReconciliationResponse.model_validate(stored.response_body)

        reconciliation = _service(session).open_reconciliation(
            booking_id=BookingId(booking_id),
            operator_id=OperatorId(operator_id),
            opened_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _reconciliation_response(reconciliation)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get(
    "/bookings/{booking_id}/reconciliation",
    response_model=FinancialReconciliationResponse,
)
def get_booking_reconciliation(
    booking_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
) -> FinancialReconciliationResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        reconciliation = _service(session).get_for_booking(
            booking_id=BookingId(booking_id),
            party=_party(buyer_id, operator_id),
        )
    return _reconciliation_response(reconciliation)


@router.get(
    "/reconciliations/{reconciliation_id}",
    response_model=FinancialReconciliationResponse,
)
def get_reconciliation(
    reconciliation_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
) -> FinancialReconciliationResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        reconciliation = _service(session).get(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            party=_party(buyer_id, operator_id),
        )
    return _reconciliation_response(reconciliation)


@router.post(
    "/reconciliations/{reconciliation_id}/invoices",
    response_model=OperatorInvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_invoice(
    reconciliation_id: UUID,
    body: OperatorInvoiceRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> OperatorInvoiceResponse:
    scope = f"POST:/v1/reconciliations/{reconciliation_id}/invoices:operator:{operator_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return OperatorInvoiceResponse.model_validate(stored.response_body)

        invoice = _service(session).submit_invoice(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            operator_id=OperatorId(operator_id),
            invoice_reference=body.invoice_reference,
            currency=Currency(body.currency.strip().upper()),
            total_amount_minor=body.total_amount_minor,
            line_items=tuple(
                InvoiceLineInput(
                    category=line.category,
                    label=line.label,
                    amount_minor=line.amount_minor,
                    reason=line.reason,
                )
                for line in body.line_items
            ),
            surcharge_reason=body.surcharge_reason,
            submitted_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _invoice_response(invoice)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.get(
    "/reconciliations/{reconciliation_id}/invoices",
    response_model=OperatorInvoiceListResponse,
)
def list_invoices(
    reconciliation_id: UUID,
    session: SessionDep,
    buyer_id: BuyerIdOptional = None,
    operator_id: OperatorIdOptional = None,
    limit: InvoiceLimit = 100,
) -> OperatorInvoiceListResponse:
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        invoices = _service(session).list_invoices(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            party=_party(buyer_id, operator_id),
            limit=limit,
        )
    return OperatorInvoiceListResponse(
        reconciliation_id=reconciliation_id,
        returned_count=len(invoices),
        invoices=[_invoice_response(invoice) for invoice in invoices],
    )


@router.post(
    "/reconciliations/{reconciliation_id}/disputes",
    response_model=DisputeResponse,
    status_code=status.HTTP_201_CREATED,
)
def dispute_invoice(
    reconciliation_id: UUID,
    body: DisputeRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> DisputeResponse:
    scope = f"POST:/v1/reconciliations/{reconciliation_id}/disputes:buyer:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return DisputeResponse.model_validate(stored.response_body)

        dispute = _service(session).dispute_invoice(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            buyer_id=OrganizationId(buyer_id),
            disputed_amount_minor=body.disputed_amount_minor,
            reason=body.reason,
            opened_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _dispute_response(dispute)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/reconciliations/{reconciliation_id}/variance-approvals",
    response_model=VarianceApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def approve_variance(
    reconciliation_id: UUID,
    body: VarianceApprovalRequest,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    buyer_id: BuyerIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> VarianceApprovalResponse:
    scope = f"POST:/v1/reconciliations/{reconciliation_id}/variance-approvals:buyer:{buyer_id}"
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return VarianceApprovalResponse.model_validate(stored.response_body)

        approval = _service(session).approve_variance(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            buyer_id=OrganizationId(buyer_id),
            approved_variance_minor=body.approved_variance_minor,
            resolves_dispute_id=(
                ReconciliationDisputeId(body.resolves_dispute_id)
                if body.resolves_dispute_id is not None
                else None
            ),
            approved_at=clock.now(),
            note=body.note,
            correlation_id=correlation_id,
        )
        response = _approval_response(approval)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/reconciliations/{reconciliation_id}/complete",
    response_model=FinancialReconciliationResponse,
)
def complete_reconciliation(
    reconciliation_id: UUID,
    session: SessionDep,
    correlation_id: CorrelationIdDep,
    operator_id: OperatorIdDep,
    idempotency_key: IdempotencyKeyDep,

    clock: Clock = Depends(get_clock),) -> FinancialReconciliationResponse:
    scope = f"POST:/v1/reconciliations/{reconciliation_id}/complete:operator:{operator_id}"
    request_hash = canonical_request_hash({})
    with session.begin():
        idempotency = SqlAlchemyIdempotencyRepository(session)
        idempotency.lock(scope, idempotency_key)
        stored = _stored_response(
            idempotency,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if stored is not None:
            return FinancialReconciliationResponse.model_validate(stored.response_body)

        reconciliation = _service(session).complete_reconciliation(
            reconciliation_id=FinancialReconciliationId(reconciliation_id),
            operator_id=OperatorId(operator_id),
            completed_at=clock.now(),
            correlation_id=correlation_id,
        )
        response = _reconciliation_response(reconciliation)
        idempotency.add(
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
    return response
