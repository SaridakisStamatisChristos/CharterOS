"""Canonical replacement-aircraft feasibility evidence.

Revision ID: 0023_replacement_feasibility
Revises: 0022_booking_termination
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0023_replacement_feasibility"
down_revision: str | None = "0022_booking_termination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FEASIBILITY_COLUMNS = (
    "feasibility_policy_version",
    "feasibility_known_as_of",
    "position_observation_id",
    "position_event_time",
    "position_recorded_at",
    "reference_profile_id",
    "reference_profile_recorded_at",
    "route_distance_tenths_nm",
    "required_range_nm",
    "reposition_distance_tenths_nm",
    "route_minutes",
    "reposition_minutes",
    "timing_buffer_minutes",
)


def upgrade() -> None:
    op.add_column(
        "disruption_proposals",
        sa.Column("feasibility_policy_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("feasibility_known_as_of", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("position_observation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("position_event_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("position_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("reference_profile_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("reference_profile_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("route_distance_tenths_nm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("required_range_nm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("reposition_distance_tenths_nm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("route_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("reposition_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "disruption_proposals",
        sa.Column("timing_buffer_minutes", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_disruption_proposals_position_observation",
        "disruption_proposals",
        "aircraft_position_observations",
        ["position_observation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_disruption_proposals_reference_profile",
        "disruption_proposals",
        "matching_reference_profiles",
        ["reference_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    all_null = " AND ".join(f"{name} IS NULL" for name in _FEASIBILITY_COLUMNS)
    all_present = " AND ".join(f"{name} IS NOT NULL" for name in _FEASIBILITY_COLUMNS)
    op.create_check_constraint(
        "ck_disruption_proposals_feasibility_evidence_complete",
        "disruption_proposals",
        f"({all_null}) OR ({all_present})",
    )
    op.create_check_constraint(
        "ck_disruption_proposals_feasibility_nonnegative",
        "disruption_proposals",
        "(route_distance_tenths_nm IS NULL OR route_distance_tenths_nm >= 0) AND "
        "(required_range_nm IS NULL OR required_range_nm >= 0) AND "
        "(reposition_distance_tenths_nm IS NULL OR reposition_distance_tenths_nm >= 0) AND "
        "(route_minutes IS NULL OR route_minutes >= 0) AND "
        "(reposition_minutes IS NULL OR reposition_minutes >= 0) AND "
        "(timing_buffer_minutes IS NULL OR timing_buffer_minutes >= 0)",
    )
    op.create_check_constraint(
        "ck_disruption_proposals_feasibility_temporal",
        "disruption_proposals",
        "feasibility_known_as_of IS NULL OR "
        "(feasibility_known_as_of <= proposed_at "
        "AND position_event_time <= feasibility_known_as_of "
        "AND position_recorded_at <= feasibility_known_as_of "
        "AND reference_profile_recorded_at <= feasibility_known_as_of)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    evidence_rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM disruption_proposals "
            "WHERE feasibility_policy_version IS NOT NULL"
        )
    ).scalar_one()
    if evidence_rows:
        raise RuntimeError(
            "refusing to downgrade 0023 while replacement feasibility evidence exists"
        )

    op.drop_constraint(
        "ck_disruption_proposals_feasibility_temporal",
        "disruption_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_disruption_proposals_feasibility_nonnegative",
        "disruption_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_disruption_proposals_feasibility_evidence_complete",
        "disruption_proposals",
        type_="check",
    )
    op.drop_constraint(
        "fk_disruption_proposals_reference_profile",
        "disruption_proposals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_disruption_proposals_position_observation",
        "disruption_proposals",
        type_="foreignkey",
    )
    for column in reversed(_FEASIBILITY_COLUMNS):
        op.drop_column("disruption_proposals", column)
