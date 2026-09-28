"""Explicit fee applicability for deterministic quote normalization

Revision ID: 0008_quote_normalization
Revises: 0007_quotes
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_quote_normalization"
down_revision: str | None = "0007_quotes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quote_price_components",
        sa.Column(
            "applicability",
            sa.String(length=16),
            nullable=False,
            server_default="known",
        ),
    )
    op.create_check_constraint(
        "ck_quote_components_applicability",
        "quote_price_components",
        "applicability IN ('known','conditional')",
    )
    op.create_check_constraint(
        "ck_quote_components_conditional_condition",
        "quote_price_components",
        "applicability = 'known' OR condition IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quote_components_conditional_condition",
        "quote_price_components",
        type_="check",
    )
    op.drop_constraint(
        "ck_quote_components_applicability",
        "quote_price_components",
        type_="check",
    )
    op.drop_column("quote_price_components", "applicability")
