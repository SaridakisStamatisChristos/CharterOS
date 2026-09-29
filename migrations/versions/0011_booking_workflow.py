"""Explicit booking workflow and transition timestamp

Revision ID: 0011_booking_workflow
Revises: 0010_contracts
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011_booking_workflow"
down_revision: str | None = "0010_contracts"
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
)


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text("UPDATE bookings SET state_changed_at = created_at"))
    op.alter_column(
        "bookings",
        "state_changed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    op.drop_constraint("ck_bookings_pr11_state", "bookings", type_="check")
    state_values = ",".join(f"'{state}'" for state in _BOOKING_STATES)
    op.create_check_constraint(
        "ck_bookings_state",
        "bookings",
        f"state IN ({state_values})",
    )
    op.create_check_constraint(
        "ck_bookings_state_changed_at",
        "bookings",
        "state_changed_at >= created_at",
    )


def downgrade() -> None:
    bind = op.get_bind()
    progressed = bind.execute(
        sa.text("SELECT count(*) FROM bookings WHERE state <> 'pending_contract'")
    ).scalar_one()
    if progressed:
        raise RuntimeError(
            "cannot downgrade PR13 while progressed booking workflow state exists; "
            "preserve booking transition history"
        )

    op.drop_constraint("ck_bookings_state_changed_at", "bookings", type_="check")
    op.drop_constraint("ck_bookings_state", "bookings", type_="check")
    op.create_check_constraint(
        "ck_bookings_pr11_state",
        "bookings",
        "state = 'pending_contract'",
    )
    op.drop_column("bookings", "state_changed_at")
