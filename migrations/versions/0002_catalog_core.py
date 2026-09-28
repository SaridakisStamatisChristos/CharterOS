"""catalog core entities and transactional outbox

Revision ID: 0002_catalog_core
Revises: 0001_repository_bootstrap
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_catalog_core"
down_revision: str | None = "0001_repository_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("legal_name_key", sa.String(length=200), nullable=False),
        sa.Column("trading_name", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(country) = 2",
            name="ck_organizations_country_length",
        ),
        sa.UniqueConstraint(
            "country",
            "legal_name_key",
            name="uq_organizations_country_legal_name",
        ),
    )

    op.create_table(
        "airports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("icao", sa.String(length=4), nullable=False),
        sa.Column("iata", sa.String(length=3), nullable=True),
        sa.Column("latitude", sa.Numeric(8, 5), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 5), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("runway_metadata", sa.JSON(), nullable=False),
        sa.Column("curfew_metadata", sa.JSON(), nullable=False),
        sa.Column("operational_flags", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name="ck_airports_latitude",
        ),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name="ck_airports_longitude",
        ),
        sa.UniqueConstraint("icao", name="uq_airports_icao"),
        sa.UniqueConstraint("iata", name="uq_airports_iata"),
    )

    op.create_table(
        "aircraft_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("manufacturer", sa.String(length=100), nullable=False),
        sa.Column("manufacturer_key", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("seats_min", sa.Integer(), nullable=False),
        sa.Column("seats_max", sa.Integer(), nullable=False),
        sa.Column("range_nm", sa.Integer(), nullable=False),
        sa.Column("runway_requirements", sa.JSON(), nullable=False),
        sa.Column("baggage_cargo_profile", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("seats_min >= 0", name="ck_aircraft_types_seats_min"),
        sa.CheckConstraint("seats_max >= seats_min", name="ck_aircraft_types_seat_bounds"),
        sa.CheckConstraint("range_nm > 0", name="ck_aircraft_types_range"),
        sa.UniqueConstraint(
            "manufacturer_key",
            "model_key",
            name="uq_aircraft_types_make_model",
        ),
    )

    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("aoc_reference", sa.String(length=80), nullable=False),
        sa.Column("operating_regions", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("insurance_status", sa.String(length=32), nullable=False),
        sa.Column("safety_documents", sa.JSON(), nullable=False),
        sa.Column("commercial_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("organization_id", name="uq_operators_organization_id"),
        sa.UniqueConstraint("aoc_reference", name="uq_operators_aoc_reference"),
    )

    op.create_table(
        "aircraft",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "operator_id",
            sa.Uuid(),
            sa.ForeignKey("operators.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("registration", sa.String(length=16), nullable=False),
        sa.Column(
            "aircraft_type_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seat_capacity", sa.Integer(), nullable=False),
        sa.Column("cargo_capacity", sa.Numeric(12, 3), nullable=False),
        sa.Column("range_nm", sa.Integer(), nullable=False),
        sa.Column(
            "home_base_id",
            sa.Uuid(),
            sa.ForeignKey("airports.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("seat_capacity > 0", name="ck_aircraft_seat_capacity"),
        sa.CheckConstraint("cargo_capacity >= 0", name="ck_aircraft_cargo_capacity"),
        sa.CheckConstraint("range_nm > 0", name="ck_aircraft_range"),
        sa.UniqueConstraint("registration", name="uq_aircraft_registration"),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(length=96), primary_key=True),
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            name="uq_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_outbox_events_aggregate_id",
        "outbox_events",
        ["aggregate_id"],
    )
    op.create_index(
        "ix_outbox_events_correlation_id",
        "outbox_events",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_correlation_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_records")
    op.drop_table("aircraft")
    op.drop_table("operators")
    op.drop_table("aircraft_types")
    op.drop_table("airports")
    op.drop_table("organizations")
