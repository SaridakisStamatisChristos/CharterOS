"""Disruption operational evidence

Revision ID: 0016_disruption_model
Revises: 0015_buyer_procurement_approvals
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0016_disruption_model"
down_revision: str | None = "0015_buyer_procurement_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disruptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("disruption_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("current_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("current_commercial_change_id", sa.Uuid(), nullable=True),
        sa.Column("latest_buyer_decision_id", sa.Uuid(), nullable=True),
        sa.Column("selected_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("selected_commercial_change_id", sa.Uuid(), nullable=True),
        sa.Column("selected_buyer_decision_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_outcome", sa.String(length=2000), nullable=True),
        sa.Column("booking_state_at_resolution", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            ondelete="RESTRICT",
            name="fk_disruptions_booking",
        ),
        sa.CheckConstraint("version > 0", name="ck_disruptions_version_positive"),
        sa.CheckConstraint(
            "disruption_type IN "
            "('delay','aircraft_unavailable','crew_unavailable','airport_restriction',"
            "'technical','weather','other')",
            name="ck_disruptions_type",
        ),
        sa.CheckConstraint(
            "status IN "
            "('open','proposed','awaiting_buyer','buyer_approved','buyer_rejected','resolved')",
            name="ck_disruptions_status",
        ),
        sa.CheckConstraint(
            "(status = 'resolved' AND selected_proposal_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND resolution_outcome IS NOT NULL "
            "AND booking_state_at_resolution IS NOT NULL) OR "
            "(status <> 'resolved' AND selected_proposal_id IS NULL "
            "AND selected_commercial_change_id IS NULL "
            "AND selected_buyer_decision_id IS NULL AND resolved_at IS NULL "
            "AND resolution_outcome IS NULL AND booking_state_at_resolution IS NULL)",
            name="ck_disruptions_resolution_lifecycle",
        ),
    )
    op.create_index(
        "ix_disruptions_booking_detected",
        "disruptions",
        ["booking_id", "detected_at", "id"],
    )

    op.create_table(
        "disruption_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("disruption_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("proposed_operator_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_aircraft_id", sa.Uuid(), nullable=False),
        sa.Column("departure_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departure_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requires_buyer_decision", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_evidence", sa.String(length=2000), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["disruption_id"],
            ["disruptions.id"],
            ondelete="RESTRICT",
            name="fk_disruption_proposals_disruption",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_proposal_id"],
            ["disruption_proposals.id"],
            ondelete="RESTRICT",
            name="fk_disruption_proposals_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_operator_id"],
            ["operators.id"],
            ondelete="RESTRICT",
            name="fk_disruption_proposals_operator",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_aircraft_id"],
            ["aircraft.id"],
            ondelete="RESTRICT",
            name="fk_disruption_proposals_aircraft",
        ),
        sa.UniqueConstraint(
            "supersedes_proposal_id",
            name="uq_disruption_proposals_supersedes",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_disruption_proposals_revision_positive",
        ),
        sa.CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_disruption_proposals_status",
        ),
        sa.CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_disruption_proposals_lifecycle",
        ),
        sa.CheckConstraint(
            "departure_start IS NULL OR "
            "(departure_end IS NOT NULL AND departure_start < departure_end)",
            name="ck_disruption_proposals_departure_window",
        ),
    )
    op.create_index(
        "uq_disruption_proposals_current",
        "disruption_proposals",
        ["disruption_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "uq_disruption_proposals_revision",
        "disruption_proposals",
        ["disruption_id", "revision_number"],
        unique=True,
    )

    op.create_table(
        "disruption_commercial_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("disruption_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_change_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("original_quote_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
        sa.Column("original_expected_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("original_worst_case_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("known_adjustment_minor", sa.BigInteger(), nullable=False),
        sa.Column("conditional_adjustment_minor", sa.BigInteger(), nullable=False),
        sa.Column("resulting_expected_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("resulting_worst_case_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("terms_summary", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["disruption_id"],
            ["disruptions.id"],
            ondelete="RESTRICT",
            name="fk_disruption_commercial_disruption",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["disruption_proposals.id"],
            ondelete="RESTRICT",
            name="fk_disruption_commercial_proposal",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_change_id"],
            ["disruption_commercial_changes.id"],
            ondelete="RESTRICT",
            name="fk_disruption_commercial_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["original_quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_disruption_commercial_quote",
        ),
        sa.UniqueConstraint(
            "supersedes_change_id",
            name="uq_disruption_commercial_supersedes",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_disruption_commercial_changes_revision_positive",
        ),
        sa.CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_disruption_commercial_changes_status",
        ),
        sa.CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_disruption_commercial_changes_lifecycle",
        ),
        sa.CheckConstraint(
            "resulting_expected_total_minor >= 0",
            name="ck_disruption_commercial_expected_nonnegative",
        ),
        sa.CheckConstraint(
            "resulting_worst_case_total_minor >= 0",
            name="ck_disruption_commercial_worst_nonnegative",
        ),
    )
    op.create_index(
        "uq_disruption_commercial_current",
        "disruption_commercial_changes",
        ["proposal_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "uq_disruption_commercial_revision",
        "disruption_commercial_changes",
        ["proposal_id", "revision_number"],
        unique=True,
    )

    op.create_table(
        "disruption_buyer_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("disruption_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("commercial_change_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_key", sa.String(length=80), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["disruption_id"],
            ["disruptions.id"],
            ondelete="RESTRICT",
            name="fk_disruption_decisions_disruption",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["disruption_proposals.id"],
            ondelete="RESTRICT",
            name="fk_disruption_decisions_proposal",
        ),
        sa.ForeignKeyConstraint(
            ["commercial_change_id"],
            ["disruption_commercial_changes.id"],
            ondelete="RESTRICT",
            name="fk_disruption_decisions_commercial",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
            name="fk_disruption_decisions_buyer",
        ),
        sa.UniqueConstraint("evidence_key", name="uq_disruption_decisions_evidence"),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_disruption_buyer_decisions_value",
        ),
    )
    op.create_index(
        "ix_disruption_buyer_decisions_disruption_time",
        "disruption_buyer_decisions",
        ["disruption_id", "decided_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM disruptions) + "
            "(SELECT count(*) FROM disruption_proposals) + "
            "(SELECT count(*) FROM disruption_commercial_changes) + "
            "(SELECT count(*) FROM disruption_buyer_decisions)"
        )
    ).scalar_one()
    if persisted:
        raise RuntimeError("refusing to downgrade 0016 while disruption evidence exists")

    op.drop_index(
        "ix_disruption_buyer_decisions_disruption_time",
        table_name="disruption_buyer_decisions",
    )
    op.drop_table("disruption_buyer_decisions")
    op.drop_index(
        "uq_disruption_commercial_revision",
        table_name="disruption_commercial_changes",
    )
    op.drop_index(
        "uq_disruption_commercial_current",
        table_name="disruption_commercial_changes",
    )
    op.drop_table("disruption_commercial_changes")
    op.drop_index("uq_disruption_proposals_revision", table_name="disruption_proposals")
    op.drop_index("uq_disruption_proposals_current", table_name="disruption_proposals")
    op.drop_table("disruption_proposals")
    op.drop_index("ix_disruptions_booking_detected", table_name="disruptions")
    op.drop_table("disruptions")
