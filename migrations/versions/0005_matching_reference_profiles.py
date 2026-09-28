"""matching reference profiles

Revision ID: 0005_matching_reference_profiles
Revises: 0004_missions
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_matching_reference_profiles"
down_revision: str | None = "0004_missions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matching_reference_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "aircraft_type_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cruise_speed_kts", sa.Integer(), nullable=False),
        sa.Column("operating_cost_per_hour_minor", sa.BigInteger(), nullable=False),
        sa.Column("operating_cost_currency", sa.String(length=3), nullable=False),
        sa.Column("max_reposition_nm", sa.Integer(), nullable=False),
        sa.Column("turnaround_buffer_minutes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("cruise_speed_kts > 0", name="ck_matching_profile_cruise_speed"),
        sa.CheckConstraint(
            "operating_cost_per_hour_minor > 0",
            name="ck_matching_profile_operating_cost",
        ),
        sa.CheckConstraint("max_reposition_nm > 0", name="ck_matching_profile_reposition"),
        sa.CheckConstraint(
            "turnaround_buffer_minutes >= 0",
            name="ck_matching_profile_turnaround",
        ),
        sa.CheckConstraint(
            "char_length(operating_cost_currency) = 3 "
            "AND operating_cost_currency = upper(operating_cost_currency)",
            name="ck_matching_profile_currency",
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= recorded_at",
            name="ck_matching_profile_superseded_after_recorded",
        ),
    )
    op.create_index(
        "uq_matching_profile_current_aircraft_type",
        "matching_reference_profiles",
        ["aircraft_type_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "ix_matching_profile_aircraft_type_recorded",
        "matching_reference_profiles",
        ["aircraft_type_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_matching_profile_aircraft_type_recorded",
        table_name="matching_reference_profiles",
    )
    op.drop_index(
        "uq_matching_profile_current_aircraft_type",
        table_name="matching_reference_profiles",
    )
    op.drop_table("matching_reference_profiles")
