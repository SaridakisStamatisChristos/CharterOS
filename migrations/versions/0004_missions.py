"""mission domain and lifecycle

Revision ID: 0004_missions
Revises: 0003_aircraft_timeline
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_missions"
down_revision: str | None = "0003_aircraft_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "buyer_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "origin_airport_id",
            sa.Uuid(),
            sa.ForeignKey("airports.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "destination_airport_id",
            sa.Uuid(),
            sa.ForeignKey("airports.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("departure_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("departure_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("max_budget_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("max_budget_currency", sa.String(length=3), nullable=True),
        sa.Column("special_requirements", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "origin_airport_id <> destination_airport_id",
            name="ck_missions_distinct_airports",
        ),
        sa.CheckConstraint("departure_to > departure_from", name="ck_missions_departure_window"),
        sa.CheckConstraint("passenger_count > 0", name="ck_missions_passenger_count"),
        sa.CheckConstraint(
            "(max_budget_amount_minor IS NULL AND max_budget_currency IS NULL) OR "
            "(max_budget_amount_minor > 0 AND max_budget_currency IS NOT NULL)",
            name="ck_missions_budget_pair",
        ),
        sa.CheckConstraint(
            "max_budget_currency IS NULL OR "
            "(char_length(max_budget_currency) = 3 AND max_budget_currency = upper(max_budget_currency))",
            name="ck_missions_budget_currency",
        ),
        sa.CheckConstraint(
            "status IN ('draft','open','sourcing','quoted','selected','contracting','booked',"
            "'operating','completed','cancelled','expired','failed')",
            name="ck_missions_status",
        ),
    )
    op.create_index(
        "ix_missions_buyer_status_departure",
        "missions",
        ["buyer_id", "status", "departure_from"],
    )
    op.create_index(
        "ix_missions_route_departure",
        "missions",
        ["origin_airport_id", "destination_airport_id", "departure_from"],
    )


def downgrade() -> None:
    op.drop_index("ix_missions_route_departure", table_name="missions")
    op.drop_index("ix_missions_buyer_status_departure", table_name="missions")
    op.drop_table("missions")
