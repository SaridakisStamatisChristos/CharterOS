"""Tender / reverse auction v1

Revision ID: 0014_tender_reverse_auction
Revises: 0013_charter_graph_projection
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0014_tender_reverse_auction"
down_revision: str | None = "0013_charter_graph_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "idempotency_records",
        "scope",
        existing_type=sa.String(length=96),
        type_=sa.String(length=192),
        existing_nullable=False,
    )

    op.create_table(
        "tenders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sealed_bid", sa.Boolean(), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("best_and_final_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awarded_quote_id", sa.Uuid(), nullable=True),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], ondelete="RESTRICT", name="fk_tenders_mission"
        ),
        sa.ForeignKeyConstraint(
            ["awarded_quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_tenders_awarded_quote",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"], ["bookings.id"], ondelete="RESTRICT", name="fk_tenders_booking"
        ),
        sa.UniqueConstraint("mission_id", name="uq_tenders_mission"),
        sa.CheckConstraint("version > 0", name="ck_tenders_version_positive"),
        sa.CheckConstraint(
            "status IN ('draft','open','best_and_final','closed','awarded')",
            name="ck_tenders_status",
        ),
        sa.CheckConstraint("opens_at < deadline_at", name="ck_tenders_window"),
        sa.CheckConstraint(
            "created_at < deadline_at", name="ck_tenders_created_before_deadline"
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND opened_at IS NULL "
            "AND best_and_final_requested_at IS NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'open' AND opened_at IS NOT NULL "
            "AND best_and_final_requested_at IS NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'best_and_final' AND opened_at IS NOT NULL "
            "AND best_and_final_requested_at IS NOT NULL AND closed_at IS NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'closed' AND opened_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND awarded_quote_id IS NULL AND booking_id IS NULL AND awarded_at IS NULL) OR "
            "(status = 'awarded' AND opened_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND awarded_quote_id IS NOT NULL AND booking_id IS NOT NULL "
            "AND awarded_at IS NOT NULL)",
            name="ck_tenders_lifecycle_shape",
        ),
        sa.CheckConstraint(
            "opened_at IS NULL OR (opened_at >= opens_at AND opened_at < deadline_at)",
            name="ck_tenders_opened_inside_window",
        ),
        sa.CheckConstraint(
            "best_and_final_requested_at IS NULL OR "
            "(best_and_final_requested_at >= opened_at "
            "AND best_and_final_requested_at < deadline_at)",
            name="ck_tenders_bafo_inside_window",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= deadline_at",
            name="ck_tenders_closed_after_deadline",
        ),
        sa.CheckConstraint(
            "awarded_at IS NULL OR awarded_at >= closed_at",
            name="ck_tenders_award_after_close",
        ),
    )
    op.create_index("ix_tenders_status_deadline", "tenders", ["status", "deadline_at"])

    op.create_table(
        "tender_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("rfq_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_quote_id", sa.Uuid(), nullable=True),
        sa.Column("best_and_final_quote_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            ondelete="RESTRICT",
            name="fk_tender_invitations_tender",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            ondelete="RESTRICT",
            name="fk_tender_invitations_operator",
        ),
        sa.ForeignKeyConstraint(
            ["rfq_id"], ["rfqs.id"], ondelete="RESTRICT", name="fk_tender_invitations_rfq"
        ),
        sa.ForeignKeyConstraint(
            ["last_quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_tender_invitations_last_quote",
        ),
        sa.ForeignKeyConstraint(
            ["best_and_final_quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_tender_invitations_bafo_quote",
        ),
        sa.UniqueConstraint("tender_id", "operator_id", name="uq_tender_invitation_operator"),
        sa.UniqueConstraint("rfq_id", name="uq_tender_invitation_rfq"),
        sa.CheckConstraint(
            "status IN ('invited','accepted','declined')",
            name="ck_tender_invitations_status",
        ),
        sa.CheckConstraint(
            "(status = 'invited' AND responded_at IS NULL) OR "
            "(status IN ('accepted','declined') AND responded_at IS NOT NULL)",
            name="ck_tender_invitations_response_shape",
        ),
        sa.CheckConstraint(
            "responded_at IS NULL OR responded_at >= invited_at",
            name="ck_tender_invitations_response_time",
        ),
        sa.CheckConstraint(
            "best_and_final_quote_id IS NULL OR best_and_final_quote_id = last_quote_id",
            name="ck_tender_invitations_bafo_is_latest",
        ),
    )
    op.create_index(
        "ix_tender_invitations_tender_status",
        "tender_invitations",
        ["tender_id", "status"],
    )

    op.create_table(
        "tender_admin_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("original_value", sa.JSON(), nullable=False),
        sa.Column("replacement_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("causation_event_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            ondelete="RESTRICT",
            name="fk_tender_corrections_tender",
        ),
        sa.ForeignKeyConstraint(
            ["causation_event_id"],
            ["outbox_events.event_id"],
            ondelete="RESTRICT",
            name="fk_tender_corrections_causation",
        ),
        sa.CheckConstraint(
            "char_length(target_type) > 0",
            name="ck_tender_correction_target_type",
        ),
        sa.CheckConstraint(
            "char_length(field_name) > 0",
            name="ck_tender_correction_field_name",
        ),
        sa.CheckConstraint(
            "char_length(reason) > 0",
            name="ck_tender_correction_reason",
        ),
    )
    op.create_index(
        "ix_tender_admin_corrections_tender_time",
        "tender_admin_corrections",
        ["tender_id", "corrected_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    evidence = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM tender_admin_corrections) + "
            "(SELECT count(*) FROM tender_invitations) + "
            "(SELECT count(*) FROM tenders) + "
            "(SELECT count(*) FROM outbox_events WHERE aggregate_type = 'tender') + "
            "(SELECT count(*) FROM idempotency_records WHERE char_length(scope) > 96)"
        )
    ).scalar_one()
    if evidence:
        raise RuntimeError(
            "cannot downgrade PR17 while tender, invitation, correction, or audit evidence exists"
        )

    op.drop_index(
        "ix_tender_admin_corrections_tender_time",
        table_name="tender_admin_corrections",
    )
    op.drop_table("tender_admin_corrections")
    op.drop_index(
        "ix_tender_invitations_tender_status",
        table_name="tender_invitations",
    )
    op.drop_table("tender_invitations")
    op.drop_index("ix_tenders_status_deadline", table_name="tenders")
    op.drop_table("tenders")
    op.alter_column(
        "idempotency_records",
        "scope",
        existing_type=sa.String(length=192),
        type_=sa.String(length=96),
        existing_nullable=False,
    )
