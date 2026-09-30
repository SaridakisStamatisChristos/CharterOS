from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE, ExcludeConstraint
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class AircraftCapacityReservationRow(Base):
    __tablename__ = "aircraft_capacity_reservations"
    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_aircraft_capacity_reservations_booking"),
        CheckConstraint("version > 0", name="ck_aircraft_capacity_reservations_version"),
        CheckConstraint(
            "status IN ('reserved','released')",
            name="ck_aircraft_capacity_reservations_status",
        ),
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_aircraft_capacity_reservations_interval",
        ),
        CheckConstraint(
            "route_distance_tenths_nm >= 0 AND route_minutes >= 0 "
            "AND turnaround_buffer_minutes >= 0",
            name="ck_aircraft_capacity_reservations_policy_inputs",
        ),
        CheckConstraint(
            "(status = 'reserved' AND released_at IS NULL AND release_reason IS NULL) OR "
            "(status = 'released' AND released_at IS NOT NULL AND release_reason IS NOT NULL)",
            name="ck_aircraft_capacity_reservations_release_evidence",
        ),
        ExcludeConstraint(
            ("aircraft_id", "="),
            ("occupied_range", "&&"),
            where=text("status = 'reserved'"),
            using="gist",
            name="ex_aircraft_capacity_reservations_reserved_overlap",
        ),
        Index(
            "ix_aircraft_capacity_reservations_aircraft_status_start",
            "aircraft_id",
            "status",
            "starts_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    booking_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occupied_range: Mapped[Range[datetime]] = mapped_column(
        TSTZRANGE,
        Computed("tstzrange(starts_at, ends_at, '[)')", persisted=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("matching_reference_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference_profile_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    route_distance_tenths_nm: Mapped[int] = mapped_column(Integer, nullable=False)
    route_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    turnaround_buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
