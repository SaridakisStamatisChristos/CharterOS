"""RFQ procurement lifecycle

Revision ID: 0006_rfqs
Revises: 0005_matching_reference_profiles
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_rfqs"
down_revision: str | None = "0005_matching_reference_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rfqs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "mission_id",
            sa.Uuid(),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "operator_id",
            sa.Uuid(),
            sa.ForeignKey("operators.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.String(length=500), nullable=True),
        sa.UniqueConstraint("mission_id", "operator_id", name="uq_rfqs_mission_operator"),
        sa.CheckConstraint(
            "status IN ('created','sent','acknowledged','quoted','declined','expired','withdrawn')",
            name="ck_rfqs_status",
        ),
        sa.CheckConstraint(
            "response_deadline IS NULL OR sent_at IS NOT NULL",
            name="ck_rfqs_deadline_requires_sent",
        ),
        sa.CheckConstraint(
            "response_deadline IS NULL OR response_deadline > sent_at",
            name="ck_rfqs_deadline_after_sent",
        ),
        sa.CheckConstraint(
            "(status = 'created' AND sent_at IS NULL AND response_deadline IS NULL "
            "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'sent' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'acknowledged' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'declined' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND declined_at IS NOT NULL AND expired_at IS NULL) OR "
            "(status = 'expired' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
            "AND expired_at IS NOT NULL AND declined_at IS NULL) OR "
            "(status IN ('quoted','withdrawn') AND sent_at IS NOT NULL "
            "AND response_deadline IS NOT NULL)",
            name="ck_rfqs_status_timestamps",
        ),
    )
    op.create_index(
        "ix_rfqs_mission_status_deadline",
        "rfqs",
        ["mission_id", "status", "response_deadline"],
    )
    op.create_index(
        "ix_rfqs_operator_status_deadline",
        "rfqs",
        ["operator_id", "status", "response_deadline"],
    )


def downgrade() -> None:
    op.drop_index("ix_rfqs_operator_status_deadline", table_name="rfqs")
    op.drop_index("ix_rfqs_mission_status_deadline", table_name="rfqs")
    op.drop_table("rfqs")
