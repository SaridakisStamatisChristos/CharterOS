"""Financial reconciliation

Revision ID: 0017_financial_reconciliation
Revises: 0016_disruption_model
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0017_financial_reconciliation"
down_revision: str | None = "0016_disruption_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_reconciliations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_quote_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quote_normalization_version", sa.String(length=64), nullable=False),
        sa.Column("quote_revision_number", sa.Integer(), nullable=False),
        sa.Column("booked_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("booked_worst_case_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_invoice_revision_id", sa.Uuid(), nullable=True),
        sa.Column("current_dispute_id", sa.Uuid(), nullable=True),
        sa.Column("current_variance_approval_id", sa.Uuid(), nullable=True),
        sa.Column("final_invoice_revision_id", sa.Uuid(), nullable=True),
        sa.Column("approved_variance_minor", sa.BigInteger(), nullable=True),
        sa.Column("final_payable_minor", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            ondelete="RESTRICT",
            name="fk_financial_reconciliations_booking",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_quote_id"],
            ["quotes.id"],
            ondelete="RESTRICT",
            name="fk_financial_reconciliations_quote",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
            name="fk_financial_reconciliations_buyer",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            ondelete="RESTRICT",
            name="fk_financial_reconciliations_operator",
        ),
        sa.UniqueConstraint("booking_id", name="uq_financial_reconciliations_booking"),
        sa.CheckConstraint(
            "version > 0",
            name="ck_financial_reconciliations_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('open','invoice_submitted','disputed','variance_approved','completed')",
            name="ck_financial_reconciliations_status",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_financial_reconciliations_currency",
        ),
        sa.CheckConstraint(
            "quote_revision_number > 0",
            name="ck_financial_reconciliations_quote_revision_positive",
        ),
        sa.CheckConstraint(
            "booked_amount_minor > 0",
            name="ck_financial_reconciliations_booked_positive",
        ),
        sa.CheckConstraint(
            "booked_worst_case_amount_minor >= booked_amount_minor",
            name="ck_financial_reconciliations_booked_worst_ge_booked",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND current_invoice_revision_id IS NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'invoice_submitted' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'disputed' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NOT NULL AND current_variance_approval_id IS NULL) OR "
            "(status = 'variance_approved' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL AND current_variance_approval_id IS NOT NULL) OR "
            "(status = 'completed' AND current_invoice_revision_id IS NOT NULL "
            "AND current_dispute_id IS NULL)",
            name="ck_financial_reconciliations_active_lifecycle",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND final_invoice_revision_id IS NOT NULL "
            "AND approved_variance_minor IS NOT NULL AND final_payable_minor IS NOT NULL "
            "AND final_payable_minor > 0 AND completed_at IS NOT NULL "
            "AND final_invoice_revision_id = current_invoice_revision_id) OR "
            "(status <> 'completed' AND final_invoice_revision_id IS NULL "
            "AND approved_variance_minor IS NULL AND final_payable_minor IS NULL "
            "AND completed_at IS NULL)",
            name="ck_financial_reconciliations_completion_evidence",
        ),
    )
    op.create_index(
        "ix_financial_reconciliations_operator_status",
        "financial_reconciliations",
        ["operator_id", "status"],
    )
    op.create_index(
        "ix_financial_reconciliations_buyer_status",
        "financial_reconciliations",
        ["buyer_id", "status"],
    )

    op.create_table(
        "operator_invoice_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_invoice_revision_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invoice_reference", sa.String(length=160), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("booked_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("variance_minor", sa.BigInteger(), nullable=False),
        sa.Column("surcharge_reason", sa.String(length=2000), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["reconciliation_id"],
            ["financial_reconciliations.id"],
            ondelete="RESTRICT",
            name="fk_operator_invoice_revisions_reconciliation",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_invoice_revision_id"],
            ["operator_invoice_revisions.id"],
            ondelete="RESTRICT",
            name="fk_operator_invoice_revisions_supersedes",
        ),
        sa.UniqueConstraint(
            "reconciliation_id",
            "revision_number",
            name="uq_operator_invoice_revisions_number",
        ),
        sa.UniqueConstraint(
            "supersedes_invoice_revision_id",
            name="uq_operator_invoice_revisions_supersedes",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_operator_invoice_revisions_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('current','superseded')",
            name="ck_operator_invoice_revisions_status",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_operator_invoice_revisions_currency",
        ),
        sa.CheckConstraint(
            "booked_amount_minor > 0 AND total_amount_minor > 0",
            name="ck_operator_invoice_revisions_amounts_positive",
        ),
        sa.CheckConstraint(
            "variance_minor = total_amount_minor - booked_amount_minor",
            name="ck_operator_invoice_revisions_variance_exact",
        ),
        sa.CheckConstraint(
            "(variance_minor <= 0) OR surcharge_reason IS NOT NULL",
            name="ck_operator_invoice_revisions_positive_variance_reason",
        ),
        sa.CheckConstraint(
            "(status = 'current' AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL)",
            name="ck_operator_invoice_revisions_lifecycle",
        ),
    )
    op.create_index(
        "uq_operator_invoice_revisions_current",
        "operator_invoice_revisions",
        ["reconciliation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )

    op.create_table(
        "operator_invoice_lines",
        sa.Column("invoice_revision_id", sa.Uuid(), primary_key=True),
        sa.Column("line_number", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["invoice_revision_id"],
            ["operator_invoice_revisions.id"],
            ondelete="RESTRICT",
            name="fk_operator_invoice_lines_invoice_revision",
        ),
        sa.CheckConstraint(
            "line_number > 0",
            name="ck_operator_invoice_lines_number_positive",
        ),
        sa.CheckConstraint(
            "amount_minor <> 0",
            name="ck_operator_invoice_lines_nonzero",
        ),
        sa.CheckConstraint(
            "category IN "
            "('charter_base','repositioning','fuel_surcharge','airport_fees','handling',"
            "'parking','crew_overnight','catering','deicing','permits','taxes',"
            "'broker_service_fee','other','credit')",
            name="ck_operator_invoice_lines_category",
        ),
        sa.CheckConstraint(
            "(category = 'credit' AND amount_minor < 0) OR "
            "(category <> 'credit' AND amount_minor > 0)",
            name="ck_operator_invoice_lines_sign",
        ),
    )

    op.create_table(
        "reconciliation_disputes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_revision_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("disputed_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reconciliation_id"],
            ["financial_reconciliations.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_disputes_reconciliation",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_revision_id"],
            ["operator_invoice_revisions.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_disputes_invoice",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_disputes_buyer",
        ),
        sa.UniqueConstraint(
            "invoice_revision_id",
            name="uq_reconciliation_disputes_invoice_revision",
        ),
        sa.CheckConstraint(
            "disputed_amount_minor > 0",
            name="ck_reconciliation_disputes_amount_positive",
        ),
    )
    op.create_index(
        "ix_reconciliation_disputes_reconciliation",
        "reconciliation_disputes",
        ["reconciliation_id", "opened_at"],
    )

    op.create_table(
        "reconciliation_variance_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_revision_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("approved_variance_minor", sa.BigInteger(), nullable=False),
        sa.Column("resolves_dispute_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["reconciliation_id"],
            ["financial_reconciliations.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_variance_approvals_reconciliation",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_revision_id"],
            ["operator_invoice_revisions.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_variance_approvals_invoice",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_variance_approvals_buyer",
        ),
        sa.ForeignKeyConstraint(
            ["resolves_dispute_id"],
            ["reconciliation_disputes.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_variance_approvals_resolves_dispute",
        ),
        sa.UniqueConstraint(
            "invoice_revision_id",
            name="uq_reconciliation_variance_approvals_invoice_revision",
        ),
        sa.UniqueConstraint(
            "resolves_dispute_id",
            name="uq_reconciliation_variance_approvals_resolves_dispute",
        ),
        sa.CheckConstraint(
            "approved_variance_minor >= 0",
            name="ck_reconciliation_variance_approvals_nonnegative",
        ),
    )
    op.create_index(
        "ix_reconciliation_variance_approvals_reconciliation",
        "reconciliation_variance_approvals",
        ["reconciliation_id", "approved_at"],
    )

    op.create_foreign_key(
        "fk_financial_reconciliations_current_invoice",
        "financial_reconciliations",
        "operator_invoice_revisions",
        ["current_invoice_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_financial_reconciliations_current_dispute",
        "financial_reconciliations",
        "reconciliation_disputes",
        ["current_dispute_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_financial_reconciliations_current_variance_approval",
        "financial_reconciliations",
        "reconciliation_variance_approvals",
        ["current_variance_approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_financial_reconciliations_final_invoice",
        "financial_reconciliations",
        "operator_invoice_revisions",
        ["final_invoice_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM financial_reconciliations) + "
            "(SELECT count(*) FROM operator_invoice_revisions) + "
            "(SELECT count(*) FROM reconciliation_disputes) + "
            "(SELECT count(*) FROM reconciliation_variance_approvals)"
        )
    ).scalar_one()
    if persisted:
        raise RuntimeError("refusing to downgrade 0017 while reconciliation evidence exists")

    op.drop_constraint(
        "fk_financial_reconciliations_final_invoice",
        "financial_reconciliations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_financial_reconciliations_current_variance_approval",
        "financial_reconciliations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_financial_reconciliations_current_dispute",
        "financial_reconciliations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_financial_reconciliations_current_invoice",
        "financial_reconciliations",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_reconciliation_variance_approvals_reconciliation",
        table_name="reconciliation_variance_approvals",
    )
    op.drop_table("reconciliation_variance_approvals")
    op.drop_index(
        "ix_reconciliation_disputes_reconciliation",
        table_name="reconciliation_disputes",
    )
    op.drop_table("reconciliation_disputes")
    op.drop_table("operator_invoice_lines")
    op.drop_index(
        "uq_operator_invoice_revisions_current",
        table_name="operator_invoice_revisions",
    )
    op.drop_table("operator_invoice_revisions")
    op.drop_index(
        "ix_financial_reconciliations_buyer_status",
        table_name="financial_reconciliations",
    )
    op.drop_index(
        "ix_financial_reconciliations_operator_status",
        table_name="financial_reconciliations",
    )
    op.drop_table("financial_reconciliations")
