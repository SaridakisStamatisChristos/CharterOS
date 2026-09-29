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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class OrganizationRow(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("length(country) = 2", name="ck_organizations_country_length"),
        UniqueConstraint("country", "legal_name_key", name="uq_organizations_country_legal_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    trading_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperatorRow(Base):
    __tablename__ = "operators"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_operators_organization_id"),
        UniqueConstraint("aoc_reference", name="uq_operators_aoc_reference"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aoc_reference: Mapped[str] = mapped_column(String(80), nullable=False)
    operating_regions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    insurance_status: Mapped[str] = mapped_column(String(32), nullable=False)
    safety_documents: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    commercial_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AirportRow(Base):
    __tablename__ = "airports"
    __table_args__ = (
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_airports_latitude"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_airports_longitude"),
        UniqueConstraint("icao", name="uq_airports_icao"),
        UniqueConstraint("iata", name="uq_airports_iata"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    iata: Mapped[str | None] = mapped_column(String(3))
    latitude: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 5), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    runway_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    curfew_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    operational_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AircraftTypeRow(Base):
    __tablename__ = "aircraft_types"
    __table_args__ = (
        CheckConstraint("seats_min >= 0", name="ck_aircraft_types_seats_min"),
        CheckConstraint("seats_max >= seats_min", name="ck_aircraft_types_seat_bounds"),
        CheckConstraint("range_nm > 0", name="ck_aircraft_types_range"),
        UniqueConstraint("manufacturer_key", "model_key", name="uq_aircraft_types_make_model"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturer_key: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    model_key: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    seats_min: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_max: Mapped[int] = mapped_column(Integer, nullable=False)
    range_nm: Mapped[int] = mapped_column(Integer, nullable=False)
    runway_requirements: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    baggage_cargo_profile: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AircraftRow(Base):
    __tablename__ = "aircraft"
    __table_args__ = (
        CheckConstraint("seat_capacity > 0", name="ck_aircraft_seat_capacity"),
        CheckConstraint("cargo_capacity >= 0", name="ck_aircraft_cargo_capacity"),
        CheckConstraint("range_nm > 0", name="ck_aircraft_range"),
        UniqueConstraint("registration", name="uq_aircraft_registration"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    registration: Mapped[str] = mapped_column(String(16), nullable=False)
    aircraft_type_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("aircraft_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seat_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    cargo_capacity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    range_nm: Mapped[int] = mapped_column(Integer, nullable=False)
    home_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"

    scope: Mapped[str] = mapped_column(String(192), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            name="uq_outbox_aggregate_version",
        ),
        CheckConstraint("publish_attempts >= 0", name="ck_outbox_publish_attempts"),
        CheckConstraint("delivery_attempts >= 0", name="ck_outbox_delivery_attempts"),
        CheckConstraint(
            "delivery_status IN ('pending','in_flight','retry','delivered','poisoned')",
            name="ck_outbox_delivery_status",
        ),
        CheckConstraint(
            "(delivery_status IN ('pending','retry') AND published_at IS NULL "
            "AND poisoned_at IS NULL AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(delivery_status = 'in_flight' AND published_at IS NULL "
            "AND poisoned_at IS NULL AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(delivery_status = 'delivered' AND published_at IS NOT NULL "
            "AND poisoned_at IS NULL AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(delivery_status = 'poisoned' AND published_at IS NULL "
            "AND poisoned_at IS NOT NULL AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_outbox_delivery_state",
        ),
        Index(
            "ix_outbox_delivery_due",
            "delivery_status",
            "available_at",
            "recorded_at",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    causation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    canonical_json: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poisoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
