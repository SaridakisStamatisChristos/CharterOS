from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.missions import MissionId
from charteros.domain.operators import OperatorId
from charteros.domain.rfqs import Rfq, RfqId, RfqStatus
from charteros.domain.shared.exceptions import OptimisticConcurrencyError
from charteros.infrastructure.db.models.rfqs import RfqRow


def _to_domain(row: RfqRow) -> Rfq:
    return Rfq(
        RfqId(row.id),
        mission_id=MissionId(row.mission_id),
        operator_id=OperatorId(row.operator_id),
        status=RfqStatus(row.status),
        created_at=row.created_at,
        response_deadline=row.response_deadline,
        sent_at=row.sent_at,
        acknowledged_at=row.acknowledged_at,
        declined_at=row.declined_at,
        expired_at=row.expired_at,
        decline_reason=row.decline_reason,
        version=row.version,
    )


class SqlAlchemyRfqRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, rfq: Rfq) -> None:
        self._session.add(
            RfqRow(
                id=rfq.id.value,
                version=rfq.version,
                mission_id=rfq.mission_id.value,
                operator_id=rfq.operator_id.value,
                status=rfq.status.value,
                created_at=rfq.created_at,
                sent_at=rfq.sent_at,
                response_deadline=rfq.response_deadline,
                acknowledged_at=rfq.acknowledged_at,
                declined_at=rfq.declined_at,
                expired_at=rfq.expired_at,
                decline_reason=rfq.decline_reason,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise EntityConflictError(
                "RFQ conflicts with persisted state; a mission/operator RFQ may already exist"
            ) from exc

    def get(self, rfq_id: RfqId) -> Rfq | None:
        row = self._session.get(RfqRow, rfq_id.value)
        return _to_domain(row) if row is not None else None

    def get_for_update(self, rfq_id: RfqId) -> Rfq | None:
        row = self._session.scalar(
            select(RfqRow).where(RfqRow.id == rfq_id.value).with_for_update()
        )
        return _to_domain(row) if row is not None else None

    def find_for_mission_operator(
        self,
        mission_id: MissionId,
        operator_id: OperatorId,
    ) -> Rfq | None:
        row = self._session.scalar(
            select(RfqRow).where(
                RfqRow.mission_id == mission_id.value,
                RfqRow.operator_id == operator_id.value,
            )
        )
        return _to_domain(row) if row is not None else None

    def list_for_mission(self, mission_id: MissionId) -> tuple[Rfq, ...]:
        rows = self._session.scalars(
            select(RfqRow)
            .where(RfqRow.mission_id == mission_id.value)
            .order_by(RfqRow.created_at, RfqRow.id)
        ).all()
        return tuple(_to_domain(row) for row in rows)

    def save(self, rfq: Rfq, *, expected_version: int) -> None:
        statement = (
            update(RfqRow)
            .where(RfqRow.id == rfq.id.value, RfqRow.version == expected_version)
            .values(
                version=rfq.version,
                status=rfq.status.value,
                sent_at=rfq.sent_at,
                response_deadline=rfq.response_deadline,
                acknowledged_at=rfq.acknowledged_at,
                declined_at=rfq.declined_at,
                expired_at=rfq.expired_at,
                decline_reason=rfq.decline_reason,
            )
            .returning(RfqRow.id)
        )
        updated_id = self._session.scalar(statement)
        if updated_id is None:
            raise OptimisticConcurrencyError(
                f"expected aggregate version {expected_version} for RFQ {rfq.id}"
            )
        self._session.flush()
