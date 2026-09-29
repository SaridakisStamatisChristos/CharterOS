from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from charteros.application.exceptions import EntityConflictError
from charteros.domain.fx import (
    CONVERSION_POLICY_VERSION,
    ROUNDING_POLICY_VERSION,
    FxLock,
    FxLockedQuote,
    FxLockId,
    FxLockStatus,
    FxRateId,
    FxRateObservation,
)
from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import ProcurementApprovalId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.domain.shared.money import Money
from charteros.infrastructure.db.models.fx import (
    FxLockConversionRow,
    FxLockRow,
    FxRateRow,
)


def _rate_to_domain(row: FxRateRow) -> FxRateObservation:
    return FxRateObservation(
        FxRateId(row.id),
        source_currency=Currency(row.source_currency),
        target_currency=Currency(row.target_currency),
        rate_text=row.rate_text,
        source_minor_exponent=row.source_minor_exponent,
        target_minor_exponent=row.target_minor_exponent,
        fx_source=row.fx_source,
        fx_source_version=row.fx_source_version,
        fx_timestamp=row.fx_timestamp,
        recorded_at=row.recorded_at,
        revision_number=row.revision_number,
        supersedes_rate_id=(
            FxRateId(row.supersedes_rate_id) if row.supersedes_rate_id is not None else None
        ),
        version=row.version,
    )


class SqlAlchemyFxRateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, rate: FxRateObservation) -> None:
        self._session.add(
            FxRateRow(
                id=rate.id.value,
                version=rate.version,
                source_currency=str(rate.source_currency),
                target_currency=str(rate.target_currency),
                rate_text=rate.rate_text,
                source_minor_exponent=rate.source_minor_exponent,
                target_minor_exponent=rate.target_minor_exponent,
                fx_source=rate.fx_source,
                fx_source_version=rate.fx_source_version,
                fx_timestamp=rate.fx_timestamp,
                recorded_at=rate.recorded_at,
                revision_number=rate.revision_number,
                supersedes_rate_id=(
                    rate.supersedes_rate_id.value if rate.supersedes_rate_id is not None else None
                ),
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(
                "FX rate observation conflicts with immutable revision lineage"
            ) from exc

    def get(self, rate_id: FxRateId) -> FxRateObservation | None:
        row = self._session.get(FxRateRow, rate_id.value)
        return _rate_to_domain(row) if row is not None else None

    def get_for_update(self, rate_id: FxRateId) -> FxRateObservation | None:
        row = self._session.scalar(
            select(FxRateRow).where(FxRateRow.id == rate_id.value).with_for_update()
        )
        return _rate_to_domain(row) if row is not None else None

    def find_successor(self, rate_id: FxRateId) -> FxRateObservation | None:
        row = self._session.scalar(
            select(FxRateRow).where(FxRateRow.supersedes_rate_id == rate_id.value)
        )
        return _rate_to_domain(row) if row is not None else None

    def latest_for_pair(
        self,
        *,
        source_currency: Currency,
        target_currency: Currency,
        fx_source: str,
        known_as_of: datetime,
    ) -> FxRateObservation | None:
        successor = aliased(FxRateRow)
        row = self._session.scalar(
            select(FxRateRow)
            .where(
                FxRateRow.source_currency == str(source_currency),
                FxRateRow.target_currency == str(target_currency),
                FxRateRow.fx_source == fx_source,
                FxRateRow.fx_timestamp <= known_as_of,
                FxRateRow.recorded_at <= known_as_of,
                ~exists(
                    select(successor.id).where(
                        successor.supersedes_rate_id == FxRateRow.id,
                        successor.recorded_at <= known_as_of,
                    )
                ),
            )
            .order_by(
                FxRateRow.fx_timestamp.desc(),
                FxRateRow.revision_number.desc(),
                FxRateRow.recorded_at.desc(),
                FxRateRow.id.desc(),
            )
            .limit(1)
        )
        return _rate_to_domain(row) if row is not None else None


class SqlAlchemyFxLockRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, lock: FxLock) -> None:
        self._session.add(
            FxLockRow(
                id=lock.id.value,
                version=lock.version,
                buyer_id=lock.buyer_id.value,
                mission_id=lock.mission_id.value,
                base_currency=str(lock.base_currency),
                fx_source=lock.fx_source,
                locked_at=lock.locked_at,
                expires_at=lock.expires_at,
                status=lock.status.value,
                integrity_digest=lock.integrity_digest,
                consumed_at=lock.consumed_at,
                consumed_approval_id=(
                    lock.consumed_approval_id.value
                    if lock.consumed_approval_id is not None
                    else None
                ),
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError("FX lock conflicts with canonical lock state") from exc

        for entry in lock.entries:
            self._session.add(
                FxLockConversionRow(
                    lock_id=lock.id.value,
                    quote_id=entry.quote_id.value,
                    quote_revision_number=entry.quote_revision_number,
                    original_currency=str(entry.original_expected.currency),
                    original_expected_minor=entry.original_expected.amount_minor,
                    original_worst_case_minor=entry.original_worst_case.amount_minor,
                    base_currency=str(entry.converted_expected.currency),
                    converted_expected_minor=entry.converted_expected.amount_minor,
                    converted_worst_case_minor=entry.converted_worst_case.amount_minor,
                    rate_id=entry.rate_id.value if entry.rate_id is not None else None,
                    rate_text=entry.rate_text,
                    fx_source=entry.fx_source,
                    fx_source_version=entry.fx_source_version,
                    fx_timestamp=entry.fx_timestamp,
                    rate_recorded_at=entry.rate_recorded_at,
                    source_minor_exponent=entry.source_minor_exponent,
                    target_minor_exponent=entry.target_minor_exponent,
                    conversion_policy_version=CONVERSION_POLICY_VERSION,
                    rounding_policy=ROUNDING_POLICY_VERSION,
                    global_rank=entry.global_rank,
                    global_score_method=entry.global_score_method,
                    global_score_total_basis_points=entry.global_score_total_basis_points,
                )
            )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError("FX lock conflicts with canonical lock state") from exc

    def get(self, lock_id: FxLockId) -> FxLock | None:
        row = self._session.get(FxLockRow, lock_id.value)
        return self._to_domain(row) if row is not None else None

    def get_for_update(self, lock_id: FxLockId) -> FxLock | None:
        row = self._session.scalar(
            select(FxLockRow).where(FxLockRow.id == lock_id.value).with_for_update()
        )
        return self._to_domain(row) if row is not None else None

    def save(self, lock: FxLock, *, expected_version: int) -> None:
        updated = self._session.scalar(
            update(FxLockRow)
            .where(FxLockRow.id == lock.id.value, FxLockRow.version == expected_version)
            .values(
                version=lock.version,
                status=lock.status.value,
                consumed_at=lock.consumed_at,
                consumed_approval_id=(
                    lock.consumed_approval_id.value
                    if lock.consumed_approval_id is not None
                    else None
                ),
            )
            .returning(FxLockRow.id)
        )
        if updated is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for FX lock {lock.id}"
            )
        self._session.flush()

    def _to_domain(self, row: FxLockRow) -> FxLock:
        conversion_rows = list(
            self._session.scalars(
                select(FxLockConversionRow)
                .where(FxLockConversionRow.lock_id == row.id)
                .order_by(
                    FxLockConversionRow.global_rank,
                    FxLockConversionRow.quote_id,
                )
            ).all()
        )
        entries = tuple(
            FxLockedQuote(
                quote_id=QuoteId(item.quote_id),
                quote_revision_number=item.quote_revision_number,
                original_expected=Money(
                    item.original_expected_minor,
                    Currency(item.original_currency),
                ),
                original_worst_case=Money(
                    item.original_worst_case_minor,
                    Currency(item.original_currency),
                ),
                converted_expected=Money(
                    item.converted_expected_minor,
                    Currency(item.base_currency),
                ),
                converted_worst_case=Money(
                    item.converted_worst_case_minor,
                    Currency(item.base_currency),
                ),
                rate_id=FxRateId(item.rate_id) if item.rate_id is not None else None,
                rate_text=item.rate_text,
                fx_source=item.fx_source,
                fx_source_version=item.fx_source_version,
                fx_timestamp=item.fx_timestamp,
                rate_recorded_at=item.rate_recorded_at,
                source_minor_exponent=item.source_minor_exponent,
                target_minor_exponent=item.target_minor_exponent,
                global_rank=item.global_rank,
                global_score_method=item.global_score_method,
                global_score_total_basis_points=item.global_score_total_basis_points,
            )
            for item in conversion_rows
        )
        return FxLock(
            FxLockId(row.id),
            buyer_id=OrganizationId(row.buyer_id),
            mission_id=MissionId(row.mission_id),
            base_currency=Currency(row.base_currency),
            fx_source=row.fx_source,
            locked_at=row.locked_at,
            expires_at=row.expires_at,
            entries=entries,
            integrity_digest=row.integrity_digest,
            status=FxLockStatus(row.status),
            consumed_at=row.consumed_at,
            consumed_approval_id=(
                ProcurementApprovalId(row.consumed_approval_id)
                if row.consumed_approval_id is not None
                else None
            ),
            version=row.version,
        )
