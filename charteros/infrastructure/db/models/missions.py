from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class MissionRow(Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint(
            "origin_airport_id <> destination_airport_id",
            name="ck_missions_distinct_airports",
        ),
        CheckConstraint("departure_to > departure_from", name="ck_missions_departure_window"),
        CheckConstraint("passenger_count > 0", name="ck_missions_passenger_count"),
        CheckConstraint(
            "(max_budget_amount_minor IS NULL AND max_budget_currency IS NULL) OR "
            "(max_budget_amount_minor > 0 AND max_budget_currency IS NOT NULL)",
            name="ck_missions_budget_pair",
        ),
        CheckConstraint(
            "max_budget_currency IS NULL OR "
            "(char_length(max_budget_currency) = 3 AND max_budget_currency = upper(max_budget_currency))",
            name="ck_missions_budget_currency",
        ),
        CheckConstraint(
            "status IN ('draft','open','sourcing','quoted','selected','contracting','booked',"
            "'operating','completed','cancelled','expired','failed')",
            name="ck_missions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin_airport_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_airport_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    departure_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    departure_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_budget_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    max_budget_currency: Mapped[str | None] = mapped_column(String(3))
    special_requirements: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
