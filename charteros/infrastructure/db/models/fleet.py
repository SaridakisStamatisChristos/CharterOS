from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class AircraftPositionObservationRow(Base):
    __tablename__ = "aircraft_position_observations"
    __table_args__ = (
        CheckConstraint(
            "(airport_id IS NOT NULL AND latitude IS NULL AND longitude IS NULL) OR "
            "(airport_id IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_aircraft_positions_location_mode",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_aircraft_positions_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_aircraft_positions_longitude",
        ),
        CheckConstraint(
            "recorded_at >= event_time",
            name="ck_aircraft_positions_recorded_after_event",
        ),
        Index(
            "ix_aircraft_positions_aircraft_event",
            "aircraft_id",
            "event_time",
            "id",
        ),
        Index(
            "ix_aircraft_positions_aircraft_recorded",
            "aircraft_id",
            "recorded_at",
            "event_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("aircraft.id", ondelete="RESTRICT"), nullable=False
    )
    airport_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("airports.id", ondelete="RESTRICT")
    )
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 5))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AircraftAvailabilityRecordRow(Base):
    __tablename__ = "aircraft_availability_records"
    __table_args__ = (
        CheckConstraint("valid_to > valid_from", name="ck_aircraft_availability_interval"),
        CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= recorded_at",
            name="ck_aircraft_availability_superseded_after_recorded",
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name="ck_aircraft_availability_not_self_superseding",
        ),
        UniqueConstraint("supersedes_id", name="uq_aircraft_availability_supersedes_once"),
        Index(
            "ix_aircraft_availability_aircraft_valid",
            "aircraft_id",
            "valid_from",
            "valid_to",
        ),
        Index(
            "ix_aircraft_availability_aircraft_recorded",
            "aircraft_id",
            "recorded_at",
        ),
        Index("ix_aircraft_availability_supersedes", "supersedes_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aircraft_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("aircraft.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft_availability_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
