"""Production-grade transactional outbox delivery

Revision ID: 0012_transactional_outbox
Revises: 0011_booking_workflow
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0012_transactional_outbox"
down_revision: str | None = "0011_booking_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("delivery_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("outbox_events", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "outbox_events",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column("outbox_events", sa.Column("lease_token", sa.Uuid(), nullable=True))
    op.add_column(
        "outbox_events",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("poisoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "delivery_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.execute(
        sa.text(
            "UPDATE outbox_events SET "
            "delivery_status = CASE WHEN published_at IS NULL THEN 'pending' ELSE 'delivered' END, "
            "available_at = COALESCE(published_at, recorded_at)"
        )
    )
    op.alter_column(
        "outbox_events",
        "delivery_status",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "available_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    op.create_check_constraint(
        "ck_outbox_publish_attempts",
        "outbox_events",
        "publish_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_outbox_delivery_attempts",
        "outbox_events",
        "delivery_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_outbox_delivery_status",
        "outbox_events",
        "delivery_status IN ('pending','in_flight','retry','delivered','poisoned')",
    )
    op.create_check_constraint(
        "ck_outbox_delivery_state",
        "outbox_events",
        "(delivery_status IN ('pending','retry') AND published_at IS NULL "
        "AND poisoned_at IS NULL AND lease_owner IS NULL AND lease_token IS NULL "
        "AND lease_expires_at IS NULL) OR "
        "(delivery_status = 'in_flight' AND published_at IS NULL "
        "AND poisoned_at IS NULL AND lease_owner IS NOT NULL "
        "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
        "(delivery_status = 'delivered' AND published_at IS NOT NULL "
        "AND poisoned_at IS NULL AND lease_owner IS NULL AND lease_token IS NULL "
        "AND lease_expires_at IS NULL) OR "
        "(delivery_status = 'poisoned' AND published_at IS NULL "
        "AND poisoned_at IS NOT NULL AND lease_owner IS NULL AND lease_token IS NULL "
        "AND lease_expires_at IS NULL)",
    )
    op.create_index(
        "ix_outbox_delivery_due",
        "outbox_events",
        ["delivery_status", "available_at", "recorded_at"],
    )

    op.create_table(
        "outbox_consumer_receipts",
        sa.Column("consumer_name", sa.String(length=128), primary_key=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("outbox_events.event_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("consumer_version", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(consumer_name) > 0",
            name="ck_outbox_consumer_receipts_name",
        ),
        sa.CheckConstraint(
            "consumer_version > 0",
            name="ck_outbox_consumer_receipts_version",
        ),
    )
    op.create_index(
        "ix_outbox_consumer_receipts_event_id",
        "outbox_consumer_receipts",
        ["event_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    receipts = bind.execute(
        sa.text("SELECT count(*) FROM outbox_consumer_receipts")
    ).scalar_one()
    delivery_history = bind.execute(
        sa.text(
            "SELECT count(*) FROM outbox_events "
            "WHERE delivery_status <> 'pending' OR publish_attempts <> 0 "
            "OR delivery_attempts <> 0"
        )
    ).scalar_one()
    if receipts or delivery_history:
        raise RuntimeError(
            "cannot downgrade PR14 while outbox delivery evidence exists; "
            "preserve delivery and consumer-deduplication history"
        )

    op.drop_index(
        "ix_outbox_consumer_receipts_event_id",
        table_name="outbox_consumer_receipts",
    )
    op.drop_table("outbox_consumer_receipts")
    op.drop_index("ix_outbox_delivery_due", table_name="outbox_events")
    op.drop_constraint("ck_outbox_delivery_state", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_delivery_status", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_delivery_attempts", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_publish_attempts", "outbox_events", type_="check")
    op.drop_column("outbox_events", "delivery_attempts")
    op.drop_column("outbox_events", "poisoned_at")
    op.drop_column("outbox_events", "lease_expires_at")
    op.drop_column("outbox_events", "lease_token")
    op.drop_column("outbox_events", "lease_owner")
    op.drop_column("outbox_events", "last_error")
    op.drop_column("outbox_events", "last_attempt_at")
    op.drop_column("outbox_events", "available_at")
    op.drop_column("outbox_events", "delivery_status")
