"""Auditable FX policy and executable buyer locks.

Revision ID: 0019_auditable_fx
Revises: 0018_audit_evidence
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0019_auditable_fx"
down_revision: str | None = "0018_audit_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_currency", sa.String(length=3), nullable=False),
        sa.Column("target_currency", sa.String(length=3), nullable=False),
        sa.Column("rate_text", sa.String(length=80), nullable=False),
        sa.Column("source_minor_exponent", sa.Integer(), nullable=False),
        sa.Column("target_minor_exponent", sa.Integer(), nullable=False),
        sa.Column("fx_source", sa.String(length=64), nullable=False),
        sa.Column("fx_source_version", sa.String(length=128), nullable=False),
        sa.Column("fx_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_rate_id",
            sa.Uuid(),
            sa.ForeignKey("fx_rates.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.CheckConstraint("version = 1", name="ck_fx_rates_single_event_version"),
        sa.CheckConstraint("revision_number >= 1", name="ck_fx_rates_revision_positive"),
        sa.CheckConstraint(
            "source_currency <> target_currency",
            name="ck_fx_rates_distinct_currencies",
        ),
        sa.CheckConstraint(
            "source_minor_exponent BETWEEN 0 AND 9",
            name="ck_fx_rates_source_minor_exponent",
        ),
        sa.CheckConstraint(
            "target_minor_exponent BETWEEN 0 AND 9",
            name="ck_fx_rates_target_minor_exponent",
        ),
        sa.CheckConstraint("fx_timestamp <= recorded_at", name="ck_fx_rates_temporal_order"),
        sa.UniqueConstraint(
            "fx_source",
            "source_currency",
            "target_currency",
            "fx_timestamp",
            "revision_number",
            name="uq_fx_rates_observation_revision",
        ),
    )
    op.create_index(
        "ix_fx_rates_resolver",
        "fx_rates",
        [
            "fx_source",
            "source_currency",
            "target_currency",
            "fx_timestamp",
            "recorded_at",
        ],
    )

    op.create_table(
        "fx_locks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "buyer_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mission_id",
            sa.Uuid(),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("fx_source", sa.String(length=64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("integrity_digest", sa.String(length=64), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "consumed_approval_id",
            sa.Uuid(),
            sa.ForeignKey("procurement_approvals.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.CheckConstraint("version IN (1, 2)", name="ck_fx_locks_version"),
        sa.CheckConstraint("status IN ('active','consumed')", name="ck_fx_locks_status"),
        sa.CheckConstraint("expires_at > locked_at", name="ck_fx_locks_window"),
        sa.CheckConstraint(
            "(status = 'active' AND consumed_at IS NULL AND consumed_approval_id IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL "
            "AND consumed_approval_id IS NOT NULL)",
            name="ck_fx_locks_lifecycle",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR "
            "(consumed_at >= locked_at AND consumed_at < expires_at)",
            name="ck_fx_locks_consumption_window",
        ),
        sa.CheckConstraint(
            "char_length(integrity_digest) = 64",
            name="ck_fx_locks_digest_length",
        ),
    )
    op.create_index(
        "ix_fx_locks_mission_time",
        "fx_locks",
        ["mission_id", "locked_at", "id"],
    )
    op.create_index(
        "ix_fx_locks_buyer_time",
        "fx_locks",
        ["buyer_id", "locked_at", "id"],
    )

    op.create_table(
        "fx_lock_conversions",
        sa.Column(
            "lock_id",
            sa.Uuid(),
            sa.ForeignKey("fx_locks.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "quote_id",
            sa.Uuid(),
            sa.ForeignKey("quotes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("quote_revision_number", sa.Integer(), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("original_expected_minor", sa.BigInteger(), nullable=False),
        sa.Column("original_worst_case_minor", sa.BigInteger(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("converted_expected_minor", sa.BigInteger(), nullable=False),
        sa.Column("converted_worst_case_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "rate_id",
            sa.Uuid(),
            sa.ForeignKey("fx_rates.id", ondelete="RESTRICT"),
        ),
        sa.Column("rate_text", sa.String(length=80), nullable=False),
        sa.Column("fx_source", sa.String(length=64), nullable=False),
        sa.Column("fx_source_version", sa.String(length=128), nullable=False),
        sa.Column("fx_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rate_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_minor_exponent", sa.Integer()),
        sa.Column("target_minor_exponent", sa.Integer()),
        sa.Column("conversion_policy_version", sa.String(length=64), nullable=False),
        sa.Column("rounding_policy", sa.String(length=64), nullable=False),
        sa.Column("global_rank", sa.Integer(), nullable=False),
        sa.Column("global_score_method", sa.String(length=96), nullable=False),
        sa.Column("global_score_total_basis_points", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "lock_id",
            "global_rank",
            name="uq_fx_lock_conversions_global_rank",
        ),
        sa.CheckConstraint(
            "quote_revision_number >= 1",
            name="ck_fx_lock_conversions_quote_revision",
        ),
        sa.CheckConstraint(
            "global_rank >= 1",
            name="ck_fx_lock_conversions_global_rank",
        ),
        sa.CheckConstraint(
            "global_score_total_basis_points >= 0",
            name="ck_fx_lock_conversions_global_score",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted_locks = bind.execute(sa.text("SELECT count(*) FROM fx_locks")).scalar_one()
    persisted_rates = bind.execute(sa.text("SELECT count(*) FROM fx_rates")).scalar_one()
    if persisted_locks or persisted_rates:
        raise RuntimeError("refusing to downgrade 0019 while immutable FX evidence exists")

    op.drop_table("fx_lock_conversions")
    op.drop_index("ix_fx_locks_buyer_time", table_name="fx_locks")
    op.drop_index("ix_fx_locks_mission_time", table_name="fx_locks")
    op.drop_table("fx_locks")
    op.drop_index("ix_fx_rates_resolver", table_name="fx_rates")
    op.drop_table("fx_rates")
