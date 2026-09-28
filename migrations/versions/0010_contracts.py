"""Contract metadata and bilateral acceptance

Revision ID: 0010_contracts
Revises: 0009_quote_acceptance_booking
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_contracts"
down_revision: str | None = "0009_quote_acceptance_booking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "booking_id",
            sa.Uuid(),
            sa.ForeignKey("bookings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "buyer_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "operator_id",
            sa.Uuid(),
            sa.ForeignKey("operators.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("document_reference", sa.String(length=1000), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("buyer_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("booking_id", name="uq_contracts_booking"),
        sa.CheckConstraint("version > 0", name="ck_contracts_version_positive"),
        sa.CheckConstraint(
            "document_version > 0",
            name="ck_contracts_document_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending_acceptance','partially_accepted','accepted')",
            name="ck_contracts_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending_acceptance' AND buyer_signed_at IS NULL "
            "AND operator_signed_at IS NULL AND accepted_at IS NULL) OR "
            "(status = 'partially_accepted' AND accepted_at IS NULL AND "
            "((buyer_signed_at IS NOT NULL AND operator_signed_at IS NULL) OR "
            "(buyer_signed_at IS NULL AND operator_signed_at IS NOT NULL))) OR "
            "(status = 'accepted' AND buyer_signed_at IS NOT NULL "
            "AND operator_signed_at IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_contracts_acceptance_state",
        ),
        sa.CheckConstraint(
            "buyer_signed_at IS NULL OR buyer_signed_at >= created_at",
            name="ck_contracts_buyer_signed_after_create",
        ),
        sa.CheckConstraint(
            "operator_signed_at IS NULL OR operator_signed_at >= created_at",
            name="ck_contracts_operator_signed_after_create",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR "
            "(accepted_at >= buyer_signed_at AND accepted_at >= operator_signed_at)",
            name="ck_contracts_accepted_after_signatures",
        ),
    )
    op.create_index("ix_contracts_status", "contracts", ["status"])
    op.create_index("ix_contracts_buyer_status", "contracts", ["buyer_id", "status"])
    op.create_index("ix_contracts_operator_status", "contracts", ["operator_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    accepted = bind.execute(
        sa.text(
            "SELECT count(*) FROM contracts "
            "WHERE buyer_signed_at IS NOT NULL OR operator_signed_at IS NOT NULL"
        )
    ).scalar_one()
    if accepted:
        raise RuntimeError(
            "cannot downgrade PR12 while contract acceptance evidence exists; "
            "preserve signed audit history"
        )

    op.drop_index("ix_contracts_operator_status", table_name="contracts")
    op.drop_index("ix_contracts_buyer_status", table_name="contracts")
    op.drop_index("ix_contracts_status", table_name="contracts")
    op.drop_table("contracts")
