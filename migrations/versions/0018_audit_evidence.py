"""Audit evidence decision snapshots.

Revision ID: 0018_audit_evidence
Revises: 0017_financial_reconciliation
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0018_audit_evidence"
down_revision: str | None = "0017_financial_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_evidence_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("source_aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("source_aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("policy_versions", sa.JSON(), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("integrity_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "decision_type",
            "source_aggregate_type",
            "source_aggregate_id",
            name="uq_decision_evidence_source",
        ),
        sa.CheckConstraint(
            "char_length(decision_type) > 0",
            name="ck_decision_evidence_decision_type",
        ),
        sa.CheckConstraint(
            "char_length(subject_type) > 0",
            name="ck_decision_evidence_subject_type",
        ),
        sa.CheckConstraint(
            "char_length(schema_version) > 0",
            name="ck_decision_evidence_schema_version",
        ),
        sa.CheckConstraint(
            "char_length(integrity_digest) = 64",
            name="ck_decision_evidence_digest_length",
        ),
    )
    op.create_index(
        "ix_decision_evidence_subject_time",
        "decision_evidence_snapshots",
        ["subject_type", "subject_id", "decided_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(
        sa.text("SELECT count(*) FROM decision_evidence_snapshots")
    ).scalar_one()
    if persisted:
        raise RuntimeError(
            "refusing to downgrade 0018 while immutable decision evidence exists"
        )
    op.drop_index(
        "ix_decision_evidence_subject_time",
        table_name="decision_evidence_snapshots",
    )
    op.drop_table("decision_evidence_snapshots")
