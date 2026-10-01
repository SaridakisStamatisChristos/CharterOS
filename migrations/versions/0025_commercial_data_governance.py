"""Commercial data-governance controls.

Revision ID: 0025_commercial_data_governance
Revises: 0024_api_abuse_bounds
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025_commercial_data_governance"
down_revision: str | None = "0024_api_abuse_bounds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_governance_legal_holds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_kind", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_digest", sa.String(length=64), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_digest", sa.String(length=64), nullable=True),
        sa.Column("release_reason", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_legal_holds_tenant_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active','released')",
            name="ck_data_governance_legal_holds_status",
        ),
        sa.CheckConstraint(
            "char_length(created_by_digest) = 64",
            name="ck_data_governance_legal_holds_creator_digest",
        ),
        sa.CheckConstraint(
            "released_by_digest IS NULL OR char_length(released_by_digest) = 64",
            name="ck_data_governance_legal_holds_releaser_digest",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND released_at IS NULL AND released_by_digest IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'released' AND released_at IS NOT NULL "
            "AND released_by_digest IS NOT NULL AND release_reason IS NOT NULL)",
            name="ck_data_governance_legal_holds_lifecycle",
        ),
    )
    op.create_index(
        "uq_data_governance_active_hold",
        "data_governance_legal_holds",
        ["tenant_kind", "tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_data_governance_legal_holds_tenant_time",
        "data_governance_legal_holds",
        ["tenant_kind", "tenant_id", "created_at"],
    )

    op.create_table(
        "data_governance_lifecycle_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_kind", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_subject_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("report", JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_kind",
            "tenant_id",
            "operation",
            "request_key_digest",
            name="uq_data_governance_lifecycle_request",
        ),
        sa.CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_lifecycle_tenant_kind",
        ),
        sa.CheckConstraint(
            "operation IN ('closure','erasure')",
            name="ck_data_governance_lifecycle_operation",
        ),
        sa.CheckConstraint(
            "status IN ('completed','blocked')",
            name="ck_data_governance_lifecycle_status",
        ),
        sa.CheckConstraint(
            "char_length(request_key_digest) = 64 AND char_length(request_hash) = 64 "
            "AND char_length(actor_subject_digest) = 64",
            name="ck_data_governance_lifecycle_digests",
        ),
        sa.CheckConstraint(
            "completed_at >= requested_at",
            name="ck_data_governance_lifecycle_time",
        ),
    )
    op.create_index(
        "ix_data_governance_lifecycle_tenant_time",
        "data_governance_lifecycle_operations",
        ["tenant_kind", "tenant_id", "requested_at"],
    )

    op.create_table(
        "data_governance_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_kind", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("related_id", sa.Uuid(), nullable=True),
        sa.Column("actor_subject_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_events_tenant_kind",
        ),
        sa.CheckConstraint(
            "char_length(event_type) > 0",
            name="ck_data_governance_events_type",
        ),
        sa.CheckConstraint(
            "char_length(actor_subject_digest) = 64",
            name="ck_data_governance_events_actor_digest",
        ),
    )
    op.create_index(
        "ix_data_governance_events_tenant_time",
        "data_governance_events",
        ["tenant_kind", "tenant_id", "recorded_at", "id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_guard_legal_hold_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'legal holds are append/release-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status = 'released' THEN
                RAISE EXCEPTION 'released legal holds are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
