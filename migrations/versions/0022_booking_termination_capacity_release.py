"""Booking termination and atomic aircraft capacity release.

Revision ID: 0022_booking_termination
Revises: 0021_aircraft_capacity
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0022_booking_termination"
down_revision: str | None = "0021_aircraft_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BOOKING_STATES = (
    "pending_contract",
    "contracted",
    "payment_pending",
    "confirmed",
    "pre_operation",
    "operating",
    "completed",
    "reconciled",
    "cancelled",
    "expired",
)


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("termination_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("termination_source", sa.String(length=16), nullable=True),
    )

    op.drop_constraint("ck_bookings_state", "bookings", type_="check")
    state_values = ",".join(f"'{state}'" for state in _BOOKING_STATES)
    op.create_check_constraint(
        "ck_bookings_state",
        "bookings",
        f"state IN ({state_values})",
    )
    op.create_check_constraint(
        "ck_bookings_termination_evidence",
        "bookings",
        "(state IN ('cancelled','expired') AND termination_reason IS NOT NULL "
        "AND termination_source IS NOT NULL) OR "
        "(state NOT IN ('cancelled','expired') AND termination_reason IS NULL "
        "AND termination_source IS NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    terminal_bookings = bind.execute(
        sa.text("SELECT count(*) FROM bookings WHERE state IN ('cancelled','expired')")
    ).scalar_one()
    released_capacity = bind.execute(
        sa.text(
            "SELECT count(*) FROM aircraft_capacity_reservations "
            "WHERE status = 'released'"
        )
    ).scalar_one()
    if terminal_bookings or released_capacity:
        raise RuntimeError(
            "refusing to downgrade 0022 while booking termination/capacity release history exists"
        )

    op.drop_constraint("ck_bookings_termination_evidence", "bookings", type_="check")
    op.drop_constraint("ck_bookings_state", "bookings", type_="check")
    previous_states = tuple(state for state in _BOOKING_STATES if state not in {"cancelled", "expired"})
    state_values = ",".join(f"'{state}'" for state in previous_states)
    op.create_check_constraint(
        "ck_bookings_state",
        "bookings",
        f"state IN ({state_values})",
    )
    op.drop_column("bookings", "termination_source")
    op.drop_column("bookings", "termination_reason")
