"""Atomic quote acceptance and minimal booking creation

Revision ID: 0009_quote_acceptance_booking
Revises: 0008_quote_normalization
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_quote_acceptance_booking"
down_revision: str | None = "0008_quote_normalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_pr11_quote_constraints() -> None:
    op.create_check_constraint(
        "ck_quotes_status",
        "quotes",
        "status IN ('submitted','accepted','rejected','expired','withdrawn','superseded')",
    )
    op.create_check_constraint(
        "ck_quotes_status_timestamps",
        "quotes",
        "(status = 'submitted' AND is_current = true "
        "AND accepted_at IS NULL AND rejected_at IS NULL "
        "AND expired_at IS NULL AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'accepted' AND is_current = false AND accepted_at IS NOT NULL "
        "AND rejected_at IS NULL AND expired_at IS NULL "
        "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'rejected' AND is_current = false AND rejected_at IS NOT NULL "
        "AND accepted_at IS NULL AND expired_at IS NULL "
        "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'expired' AND is_current = false AND expired_at IS NOT NULL "
        "AND accepted_at IS NULL AND rejected_at IS NULL "
        "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'withdrawn' AND is_current = false AND withdrawn_at IS NOT NULL "
        "AND accepted_at IS NULL AND rejected_at IS NULL "
        "AND expired_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'superseded' AND is_current = false AND superseded_at IS NOT NULL "
        "AND accepted_at IS NULL AND rejected_at IS NULL "
        "AND expired_at IS NULL AND withdrawn_at IS NULL)",
    )


def _create_pr10_quote_constraints() -> None:
    op.create_check_constraint(
        "ck_quotes_status",
        "quotes",
        "status IN ('submitted','expired','withdrawn','superseded')",
    )
    op.create_check_constraint(
        "ck_quotes_status_timestamps",
        "quotes",
        "(status = 'submitted' AND is_current = true "
        "AND expired_at IS NULL AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'expired' AND is_current = false AND expired_at IS NOT NULL "
        "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'withdrawn' AND is_current = false AND withdrawn_at IS NOT NULL "
        "AND expired_at IS NULL AND superseded_at IS NULL) OR "
        "(status = 'superseded' AND is_current = false AND superseded_at IS NOT NULL "
        "AND expired_at IS NULL AND withdrawn_at IS NULL)",
    )


def upgrade() -> None:
    op.add_column("quotes", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quotes", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_quotes_status_timestamps", "quotes", type_="check")
    op.drop_constraint("ck_quotes_status", "quotes", type_="check")
    _create_pr11_quote_constraints()

    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "mission_id",
            sa.Uuid(),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "accepted_quote_id",
            sa.Uuid(),
            sa.ForeignKey("quotes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "operator_id",
            sa.Uuid(),
            sa.ForeignKey("operators.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "aircraft_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mission_id", name="uq_bookings_mission"),
        sa.UniqueConstraint("accepted_quote_id", name="uq_bookings_accepted_quote"),
        sa.CheckConstraint("version > 0", name="ck_bookings_version_positive"),
        sa.CheckConstraint("state = 'pending_contract'", name="ck_bookings_pr11_state"),
    )
    op.create_index("ix_bookings_operator_state", "bookings", ["operator_id", "state"])
    op.create_index("ix_bookings_aircraft_state", "bookings", ["aircraft_id", "state"])


def downgrade() -> None:
    bind = op.get_bind()
    awarded = bind.execute(
        sa.text("SELECT count(*) FROM quotes WHERE status IN ('accepted','rejected')")
    ).scalar_one()
    if awarded:
        raise RuntimeError(
            "cannot downgrade PR11 while accepted/rejected quote history exists; "
            "preserve the award audit trail"
        )

    op.drop_index("ix_bookings_aircraft_state", table_name="bookings")
    op.drop_index("ix_bookings_operator_state", table_name="bookings")
    op.drop_table("bookings")

    op.drop_constraint("ck_quotes_status_timestamps", "quotes", type_="check")
    op.drop_constraint("ck_quotes_status", "quotes", type_="check")
    op.drop_column("quotes", "rejected_at")
    op.drop_column("quotes", "accepted_at")
    _create_pr10_quote_constraints()
