"""Quote submission, revision, expiry, and withdrawal lifecycle

Revision ID: 0007_quotes
Revises: 0006_rfqs
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_quotes"
down_revision: str | None = "0006_rfqs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_rfq_status_constraint() -> None:
    op.create_check_constraint(
        "ck_rfqs_status_timestamps",
        "rfqs",
        "(status = 'created' AND sent_at IS NULL AND response_deadline IS NULL "
        "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'sent' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
        "AND acknowledged_at IS NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'acknowledged' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
        "AND acknowledged_at IS NOT NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'quoted' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
        "AND acknowledged_at IS NOT NULL AND declined_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'declined' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
        "AND declined_at IS NOT NULL AND expired_at IS NULL) OR "
        "(status = 'expired' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL "
        "AND expired_at IS NOT NULL AND declined_at IS NULL) OR "
        "(status = 'withdrawn' AND sent_at IS NOT NULL AND response_deadline IS NOT NULL)",
    )


def _create_pr7_rfq_status_constraint() -> None:
    op.create_check_constraint(
        "ck_rfqs_status_timestamps",
        "rfqs",
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
    )


def upgrade() -> None:
    op.drop_constraint("ck_rfqs_status_timestamps", "rfqs", type_="check")
    _create_rfq_status_constraint()

    op.create_table(
        "quotes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "rfq_id",
            sa.Uuid(),
            sa.ForeignKey("rfqs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "aircraft_id",
            sa.Uuid(),
            sa.ForeignKey("aircraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("base_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("repositioning_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("inclusions", sa.JSON(), nullable=False),
        sa.Column("exclusions", sa.JSON(), nullable=False),
        sa.Column("cancellation_terms", sa.Text(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_quote_id",
            sa.Uuid(),
            sa.ForeignKey("quotes.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("rfq_id", "revision_number", name="uq_quotes_rfq_revision"),
        sa.UniqueConstraint("supersedes_quote_id", name="uq_quotes_supersedes_quote"),
        sa.CheckConstraint(
            "status IN ('submitted','expired','withdrawn','superseded')",
            name="ck_quotes_status",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="ck_quotes_currency",
        ),
        sa.CheckConstraint("base_amount_minor > 0", name="ck_quotes_base_positive"),
        sa.CheckConstraint(
            "repositioning_amount_minor IS NULL OR repositioning_amount_minor >= 0",
            name="ck_quotes_repositioning_nonnegative",
        ),
        sa.CheckConstraint("revision_number > 0", name="ck_quotes_revision_positive"),
        sa.CheckConstraint("valid_until > submitted_at", name="ck_quotes_validity_window"),
        sa.CheckConstraint(
            "(revision_number = 1 AND supersedes_quote_id IS NULL) OR "
            "(revision_number > 1 AND supersedes_quote_id IS NOT NULL)",
            name="ck_quotes_revision_lineage",
        ),
        sa.CheckConstraint(
            "(status = 'submitted' AND is_current = true "
            "AND expired_at IS NULL AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'expired' AND is_current = false AND expired_at IS NOT NULL "
            "AND withdrawn_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'withdrawn' AND is_current = false AND withdrawn_at IS NOT NULL "
            "AND expired_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND is_current = false AND superseded_at IS NOT NULL "
            "AND expired_at IS NULL AND withdrawn_at IS NULL)",
            name="ck_quotes_status_timestamps",
        ),
    )
    op.create_index("ix_quotes_rfq_revision", "quotes", ["rfq_id", "revision_number"])
    op.create_index("ix_quotes_aircraft_status", "quotes", ["aircraft_id", "status"])
    op.create_index(
        "uq_quotes_current_rfq",
        "quotes",
        ["rfq_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "quote_price_components",
        sa.Column(
            "quote_id",
            sa.Uuid(),
            sa.ForeignKey("quotes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("line_number", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("condition", sa.String(length=500), nullable=True),
        sa.CheckConstraint("line_number >= 0", name="ck_quote_components_line_number"),
        sa.CheckConstraint(
            "amount_minor >= 0",
            name="ck_quote_components_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "category IN ('fuel_surcharge','airport_fees','handling','parking',"
            "'crew_overnight','catering','deicing','permits','taxes',"
            "'broker_service_fee','other')",
            name="ck_quote_components_category",
        ),
    )


def downgrade() -> None:
    op.drop_table("quote_price_components")
    op.drop_index("uq_quotes_current_rfq", table_name="quotes")
    op.drop_index("ix_quotes_aircraft_status", table_name="quotes")
    op.drop_index("ix_quotes_rfq_revision", table_name="quotes")
    op.drop_table("quotes")

    op.drop_constraint("ck_rfqs_status_timestamps", "rfqs", type_="check")
    _create_pr7_rfq_status_constraint()
