from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class MatchingReferenceProfileRow(Base):
    __tablename__ = "matching_reference_profiles"
    __table_args__ = (
        CheckConstraint("cruise_speed_kts > 0", name="ck_matching_profile_cruise_speed"),
        CheckConstraint(
            "operating_cost_per_hour_minor > 0",
            name="ck_matching_profile_operating_cost",
        ),
        CheckConstraint("max_reposition_nm > 0", name="ck_matching_profile_reposition"),
        CheckConstraint(
            "turnaround_buffer_minutes >= 0",
            name="ck_matching_profile_turnaround",
        ),
        CheckConstraint(
            "char_length(operating_cost_currency) = 3 "
            "AND operating_cost_currency = upper(operating_cost_currency)",
            name="ck_matching_profile_currency",
        ),
        CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= recorded_at",
            name="ck_matching_profile_superseded_after_recorded",
        ),
        Index(
            "uq_matching_profile_current_aircraft_type",
            "aircraft_type_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_matching_profile_aircraft_type_recorded",
            "aircraft_type_id",
            "recorded_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aircraft_type_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cruise_speed_kts: Mapped[int] = mapped_column(Integer, nullable=False)
    operating_cost_per_hour_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operating_cost_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_reposition_nm: Mapped[int] = mapped_column(Integer, nullable=False)
    turnaround_buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
