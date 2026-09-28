"""aircraft position and availability timeline

Revision ID: 0003_aircraft_timeline
Revises: 0002_catalog_core
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_aircraft_timeline"
down_revision: str | None = "0002_catalog_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "aircraft_position_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "aircraft_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "airport_id",
            sa.Uuid(),
            sa.ForeignKey("airports.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("latitude", sa.Numeric(8, 5), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 5), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(airport_id IS NOT NULL AND latitude IS NULL AND longitude IS NULL) OR "
            "(airport_id IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_aircraft_positions_location_mode",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_aircraft_positions_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_aircraft_positions_longitude",
        ),
        sa.CheckConstraint(
            "recorded_at >= event_time",
            name="ck_aircraft_positions_recorded_after_event",
        ),
    )
    op.create_index(
        "ix_aircraft_positions_aircraft_event",
        "aircraft_position_observations",
        ["aircraft_id", "event_time", "id"],
    )
    op.create_index(
        "ix_aircraft_positions_aircraft_recorded",
        "aircraft_position_observations",
        ["aircraft_id", "recorded_at", "event_time"],
    )

    op.create_table(
        "aircraft_availability_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "aircraft_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft_availability_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("valid_to > valid_from", name="ck_aircraft_availability_interval"),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= recorded_at",
            name="ck_aircraft_availability_superseded_after_recorded",
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name="ck_aircraft_availability_not_self_superseding",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_aircraft_availability_supersedes_once"),
    )
    op.create_index(
        "ix_aircraft_availability_aircraft_valid",
        "aircraft_availability_records",
        ["aircraft_id", "valid_from", "valid_to"],
    )
    op.create_index(
        "ix_aircraft_availability_aircraft_recorded",
        "aircraft_availability_records",
        ["aircraft_id", "recorded_at"],
    )
    op.create_index(
        "ix_aircraft_availability_supersedes",
        "aircraft_availability_records",
        ["supersedes_id"],
    )
    op.execute(
        """
        ALTER TABLE aircraft_availability_records
        ADD CONSTRAINT ex_aircraft_availability_current_no_overlap
        EXCLUDE USING gist (
            aircraft_id WITH =,
            tstzrange(valid_from, valid_to, '[)') WITH &&
        )
        WHERE (superseded_at IS NULL)
        """
    )


def downgrade() -> None:
    op.drop_table("aircraft_availability_records")
    op.drop_index(
        "ix_aircraft_positions_aircraft_recorded",
        table_name="aircraft_position_observations",
    )
    op.drop_index(
        "ix_aircraft_positions_aircraft_event",
        table_name="aircraft_position_observations",
    )
    op.drop_table("aircraft_position_observations")
