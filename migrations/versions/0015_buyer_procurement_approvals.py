"""Buyer procurement approval evidence

Revision ID: 0015_buyer_procurement_approvals
Revises: 0014_tender_reverse_auction
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0015_buyer_procurement_approvals"
down_revision: str | None = "0014_tender_reverse_auction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("supersedes_approval_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            ondelete="RESTRICT",
            name="fk_procurement_approvals_mission",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
            name="fk_procurement_approvals_buyer",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_procurement_approvals_quote",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_approval_id"],
            ["procurement_approvals.id"],
            ondelete="RESTRICT",
            name="fk_procurement_approvals_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            ondelete="RESTRICT",
            name="fk_procurement_approvals_booking",
        ),
        sa.UniqueConstraint(
            "supersedes_approval_id",
            name="uq_procurement_approvals_supersedes",
        ),
        sa.UniqueConstraint("booking_id", name="uq_procurement_approvals_booking"),
        sa.CheckConstraint(
            "version > 0",
            name="ck_procurement_approvals_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('approved','superseded','consumed')",
            name="ck_procurement_approvals_status",
        ),
        sa.CheckConstraint(
            "(status = 'approved' AND superseded_at IS NULL AND consumed_at IS NULL "
            "AND booking_id IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL AND consumed_at IS NULL "
            "AND booking_id IS NULL) OR "
            "(status = 'consumed' AND superseded_at IS NULL AND consumed_at IS NOT NULL "
            "AND booking_id IS NOT NULL)",
            name="ck_procurement_approvals_lifecycle",
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= approved_at",
            name="ck_procurement_approvals_superseded_after_approval",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= approved_at",
            name="ck_procurement_approvals_consumed_after_approval",
        ),
    )
    op.create_index(
        "uq_procurement_approvals_active_mission",
        "procurement_approvals",
        ["mission_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_index(
        "ix_procurement_approvals_mission_time",
        "procurement_approvals",
        ["mission_id", "approved_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(sa.text("SELECT count(*) FROM procurement_approvals")).scalar_one()
    if persisted:
        raise RuntimeError(
            "refusing to downgrade 0015 while procurement approval evidence exists"
        )
    op.drop_index(
        "ix_procurement_approvals_mission_time",
        table_name="procurement_approvals",
    )
    op.drop_index(
        "uq_procurement_approvals_active_mission",
        table_name="procurement_approvals",
    )
    op.drop_table("procurement_approvals")
