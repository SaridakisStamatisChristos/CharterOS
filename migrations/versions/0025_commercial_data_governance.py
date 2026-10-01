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
            IF OLD.id IS DISTINCT FROM NEW.id
                OR OLD.tenant_kind IS DISTINCT FROM NEW.tenant_kind
                OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
                OR OLD.reason IS DISTINCT FROM NEW.reason
                OR OLD.created_at IS DISTINCT FROM NEW.created_at
                OR OLD.created_by_digest IS DISTINCT FROM NEW.created_by_digest
            THEN
                RAISE EXCEPTION 'legal hold identity/scope is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status <> 'released'
                OR NEW.released_at IS NULL
                OR NEW.released_by_digest IS NULL
                OR NEW.release_reason IS NULL
            THEN
                RAISE EXCEPTION 'legal hold update must be a complete release transition'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_data_governance_legal_hold_guard
        BEFORE UPDATE OR DELETE ON data_governance_legal_holds
        FOR EACH ROW EXECUTE FUNCTION charteros_guard_legal_hold_mutation()
        """
    )
    op.execute(
        "ALTER TABLE data_governance_legal_holds "
        "ENABLE ALWAYS TRIGGER trg_data_governance_legal_hold_guard"
    )

    for table_name, tag, primary_keys in (
        ("data_governance_lifecycle_operations", "governance_lifecycle", "id"),
        ("data_governance_events", "governance_event", "id"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_ei_{tag}_guard
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION charteros_guard_evidence_mutation('')
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_ei_{tag}_capture
            AFTER INSERT ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION charteros_capture_evidence_insert(
                '{primary_keys}', 'tenant_kind,tenant_id', ''
            )
            """
        )
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER trg_ei_{tag}_guard")
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER trg_ei_{tag}_capture")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_apply_runtime_governance_privileges(
            p_role name
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = p_role::text) THEN
                RAISE EXCEPTION 'database role % does not exist', p_role;
            END IF;

            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON TABLE public.data_governance_legal_holds TO %I',
                p_role
            );
            EXECUTE format(
                'REVOKE DELETE ON TABLE public.data_governance_legal_holds FROM %I', p_role
            );

            EXECUTE format(
                'GRANT SELECT, INSERT ON TABLE public.data_governance_lifecycle_operations, '
                'public.data_governance_events TO %I', p_role
            );
            EXECUTE format(
                'REVOKE UPDATE, DELETE ON TABLE public.data_governance_lifecycle_operations, '
                'public.data_governance_events FROM %I', p_role
            );
        END
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION charteros_apply_runtime_governance_privileges(name) FROM PUBLIC"
    )


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM data_governance_legal_holds) + "
            "(SELECT count(*) FROM data_governance_lifecycle_operations) + "
            "(SELECT count(*) FROM data_governance_events) + "
            "(SELECT count(*) FROM evidence_integrity_entries "
            " WHERE source_table IN "
            " ('data_governance_lifecycle_operations','data_governance_events'))"
        )
    ).scalar_one()
    if persisted:
        raise RuntimeError(
            "refusing to downgrade 0025 while data-governance audit/evidence history exists"
        )

    op.execute(
        "DROP FUNCTION IF EXISTS charteros_apply_runtime_governance_privileges(name)"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_governance_event_capture ON data_governance_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_governance_event_guard ON data_governance_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_governance_lifecycle_capture "
        "ON data_governance_lifecycle_operations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_governance_lifecycle_guard "
        "ON data_governance_lifecycle_operations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_data_governance_legal_hold_guard "
        "ON data_governance_legal_holds"
    )
    op.execute("DROP FUNCTION IF EXISTS charteros_guard_legal_hold_mutation()")
    op.drop_table("data_governance_events")
    op.drop_table("data_governance_lifecycle_operations")
    op.drop_table("data_governance_legal_holds")
