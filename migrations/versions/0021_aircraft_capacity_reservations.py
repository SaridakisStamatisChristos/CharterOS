"""Aircraft capacity reservations and PostgreSQL overlap exclusion.

Revision ID: 0021_aircraft_capacity
Revises: 0020_evidence_integrity
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0021_aircraft_capacity"
down_revision: str | None = "0020_evidence_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "aircraft_capacity_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "occupied_range",
            postgresql.TSTZRANGE(),
            sa.Computed("tstzrange(starts_at, ends_at, '[)')", persisted=True),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("reference_profile_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reference_profile_recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("route_distance_tenths_nm", sa.Integer(), nullable=False),
        sa.Column("route_minutes", sa.Integer(), nullable=False),
        sa.Column("turnaround_buffer_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name="ck_aircraft_capacity_reservations_version",
        ),
        sa.CheckConstraint(
            "status IN ('reserved','released')",
            name="ck_aircraft_capacity_reservations_status",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at",
            name="ck_aircraft_capacity_reservations_interval",
        ),
        sa.CheckConstraint(
            "route_distance_tenths_nm >= 0 AND route_minutes >= 0 "
            "AND turnaround_buffer_minutes >= 0",
            name="ck_aircraft_capacity_reservations_policy_inputs",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND released_at IS NULL AND release_reason IS NULL) OR "
            "(status = 'released' AND released_at IS NOT NULL AND release_reason IS NOT NULL)",
            name="ck_aircraft_capacity_reservations_release_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["aircraft_id"],
            ["aircraft.id"],
            name="fk_aircraft_capacity_reservations_aircraft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name="fk_aircraft_capacity_reservations_booking",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_aircraft_capacity_reservations_mission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name="fk_aircraft_capacity_reservations_operator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reference_profile_id"],
            ["matching_reference_profiles.id"],
            name="fk_aircraft_capacity_reservations_reference_profile",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "booking_id",
            name="uq_aircraft_capacity_reservations_booking",
        ),
        postgresql.ExcludeConstraint(
            ("aircraft_id", "="),
            ("occupied_range", "&&"),
            where=sa.text("status = 'reserved'"),
            using="gist",
            name="ex_aircraft_capacity_reservations_reserved_overlap",
        ),
    )
    op.create_index(
        "ix_aircraft_capacity_reservations_aircraft_status_start",
        "aircraft_capacity_reservations",
        ["aircraft_id", "status", "starts_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION charteros_guard_capacity_reservation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'aircraft capacity reservations cannot be deleted';
            END IF;

            IF ROW(
                OLD.id,
                OLD.aircraft_id,
                OLD.booking_id,
                OLD.mission_id,
                OLD.operator_id,
                OLD.starts_at,
                OLD.ends_at,
                OLD.created_at,
                OLD.policy_version,
                OLD.reference_profile_id,
                OLD.reference_profile_recorded_at,
                OLD.route_distance_tenths_nm,
                OLD.route_minutes,
                OLD.turnaround_buffer_minutes
            ) IS DISTINCT FROM ROW(
                NEW.id,
                NEW.aircraft_id,
                NEW.booking_id,
                NEW.mission_id,
                NEW.operator_id,
                NEW.starts_at,
                NEW.ends_at,
                NEW.created_at,
                NEW.policy_version,
                NEW.reference_profile_id,
                NEW.reference_profile_recorded_at,
                NEW.route_distance_tenths_nm,
                NEW.route_minutes,
                NEW.turnaround_buffer_minutes
            ) THEN
                RAISE EXCEPTION 'aircraft capacity reservation identity/evidence is immutable';
            END IF;

            IF OLD.status = 'released' AND ROW(
                NEW.status,
                NEW.released_at,
                NEW.release_reason
            ) IS DISTINCT FROM ROW(
                OLD.status,
                OLD.released_at,
                OLD.release_reason
            ) THEN
                RAISE EXCEPTION 'released aircraft capacity reservation is immutable';
            END IF;

            IF OLD.status = 'reserved' AND NEW.status NOT IN ('reserved', 'released') THEN
                RAISE EXCEPTION 'invalid aircraft capacity reservation transition';
            END IF;

            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_aircraft_capacity_reservation_guard
        BEFORE UPDATE OR DELETE ON aircraft_capacity_reservations
        FOR EACH ROW EXECUTE FUNCTION charteros_guard_capacity_reservation()
        """
    )
    op.execute(
        "ALTER TABLE aircraft_capacity_reservations "
        "ENABLE ALWAYS TRIGGER trg_aircraft_capacity_reservation_guard"
    )
    op.execute(
        """
        CREATE TRIGGER trg_ei_aircraft_capacity_capture
        AFTER INSERT ON aircraft_capacity_reservations
        FOR EACH ROW
        EXECUTE FUNCTION charteros_capture_evidence_insert(
            'id',
            'mission_id',
            'version,status,released_at,release_reason'
        )
        """
    )
    op.execute(
        "ALTER TABLE aircraft_capacity_reservations "
        "ENABLE ALWAYS TRIGGER trg_ei_aircraft_capacity_capture"
    )

    op.execute(
        """
        DO $
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'charteros_runtime') THEN
                PERFORM charteros_apply_runtime_evidence_privileges('charteros_runtime');
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT count(*) FROM aircraft_capacity_reservations")
    ).scalar_one():
        raise RuntimeError(
            "refusing to downgrade 0021 while aircraft capacity reservations exist"
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_aircraft_capacity_capture "
        "ON aircraft_capacity_reservations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_aircraft_capacity_reservation_guard "
        "ON aircraft_capacity_reservations"
    )
    op.execute("DROP FUNCTION IF EXISTS charteros_guard_capacity_reservation()")
    op.drop_index(
        "ix_aircraft_capacity_reservations_aircraft_status_start",
        table_name="aircraft_capacity_reservations",
    )
    op.drop_table("aircraft_capacity_reservations")
