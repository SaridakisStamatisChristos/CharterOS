"""Database-enforced evidence integrity and bounded tamper-evidence streams.

Revision ID: 0020_evidence_integrity
Revises: 0019_auditable_fx
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0020_evidence_integrity"
down_revision: str | None = "0019_auditable_fx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROTECTED_TABLES: tuple[
    tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str],
    ...,
] = (
    (
        "outbox_events",
        "outbox",
        ("event_id",),
        ("aggregate_type", "aggregate_id"),
        (
            "published_at",
            "publish_attempts",
            "delivery_status",
            "available_at",
            "last_attempt_at",
            "last_error",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "poisoned_at",
            "delivery_attempts",
        ),
        "aggregate_type, aggregate_id, aggregate_version, event_id",
    ),
    (
        "decision_evidence_snapshots",
        "decision",
        ("id",),
        ("subject_type", "subject_id"),
        (),
        "subject_type, subject_id, decided_at, id",
    ),
    (
        "fx_rates",
        "fx_rate",
        ("id",),
        ("fx_source", "source_currency", "target_currency", "fx_timestamp"),
        (),
        "fx_source, source_currency, target_currency, fx_timestamp, revision_number, id",
    ),
    (
        "fx_locks",
        "fx_lock",
        ("id",),
        ("mission_id",),
        ("version", "status", "consumed_at", "consumed_approval_id"),
        "mission_id, locked_at, id",
    ),
    (
        "fx_lock_conversions",
        "fx_conversion",
        ("lock_id", "quote_id"),
        ("lock_id",),
        (),
        "lock_id, global_rank, quote_id",
    ),
    (
        "procurement_approvals",
        "approval",
        ("id",),
        ("mission_id",),
        ("version", "status", "superseded_at", "consumed_at", "booking_id"),
        "mission_id, approved_at, id",
    ),
    (
        "contracts",
        "contract",
        ("id",),
        ("booking_id",),
        ("version", "status", "buyer_signed_at", "operator_signed_at", "accepted_at"),
        "booking_id, created_at, id",
    ),
    (
        "disruption_proposals",
        "disruption_proposal",
        ("id",),
        ("disruption_id",),
        ("status", "superseded_at"),
        "disruption_id, revision_number, id",
    ),
    (
        "disruption_commercial_changes",
        "disruption_commercial",
        ("id",),
        ("proposal_id",),
        ("status", "superseded_at"),
        "proposal_id, revision_number, id",
    ),
    (
        "disruption_buyer_decisions",
        "disruption_decision",
        ("id",),
        ("disruption_id",),
        (),
        "disruption_id, decided_at, id",
    ),
    (
        "financial_reconciliations",
        "reconciliation",
        ("id",),
        ("booking_id",),
        (
            "version",
            "status",
            "current_invoice_revision_id",
            "current_dispute_id",
            "current_variance_approval_id",
            "final_invoice_revision_id",
            "approved_variance_minor",
            "final_payable_minor",
            "completed_at",
        ),
        "booking_id, opened_at, id",
    ),
    (
        "operator_invoice_revisions",
        "invoice",
        ("id",),
        ("reconciliation_id",),
        ("status", "superseded_at"),
        "reconciliation_id, revision_number, id",
    ),
    (
        "operator_invoice_lines",
        "invoice_line",
        ("invoice_revision_id", "line_number"),
        ("invoice_revision_id",),
        (),
        "invoice_revision_id, line_number",
    ),
    (
        "reconciliation_disputes",
        "reconciliation_dispute",
        ("id",),
        ("reconciliation_id",),
        (),
        "reconciliation_id, opened_at, id",
    ),
    (
        "reconciliation_variance_approvals",
        "variance_approval",
        ("id",),
        ("reconciliation_id",),
        (),
        "reconciliation_id, approved_at, id",
    ),
    (
        "tender_admin_corrections",
        "tender_correction",
        ("id",),
        ("tender_id",),
        (),
        "tender_id, corrected_at, id",
    ),
)


def _text_array(items: tuple[str, ...]) -> str:
    if not items:
        return "ARRAY[]::text[]"
    values = ", ".join("'" + item.replace("'", "''") + "'" for item in items)
    return f"ARRAY[{values}]::text[]"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "evidence_integrity_entries",
        sa.Column("stream_key", sa.Text(), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_key", JSONB(), nullable=False),
        sa.Column("canonical_payload", JSONB(), nullable=False),
        sa.Column("previous_digest", sa.String(length=64), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint(
            "source_table",
            "source_key",
            name="uq_evidence_integrity_source",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_evidence_integrity_sequence_positive",
        ),
        sa.CheckConstraint(
            "previous_digest IS NULL OR char_length(previous_digest) = 64",
            name="ck_evidence_integrity_previous_digest",
        ),
        sa.CheckConstraint(
            "char_length(digest) = 64",
            name="ck_evidence_integrity_digest",
        ),
    )
    op.create_index(
        "ix_evidence_integrity_source",
        "evidence_integrity_entries",
        ["source_table"],
    )

    op.create_table(
        "evidence_integrity_checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("stream_key", sa.Text(), nullable=False),
        sa.Column("through_sequence", sa.BigInteger(), nullable=False),
        sa.Column("root_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["stream_key", "through_sequence"],
            ["evidence_integrity_entries.stream_key", "evidence_integrity_entries.sequence"],
            ondelete="RESTRICT",
            name="fk_evidence_checkpoint_entry",
        ),
        sa.UniqueConstraint(
            "stream_key",
            "through_sequence",
            name="uq_evidence_checkpoint_stream_sequence",
        ),
        sa.CheckConstraint(
            "through_sequence > 0",
            name="ck_evidence_checkpoint_sequence_positive",
        ),
        sa.CheckConstraint(
            "char_length(root_digest) = 64",
            name="ck_evidence_checkpoint_digest",
        ),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_jsonb_pick(
            p_document jsonb,
            p_keys text[]
        ) RETURNS jsonb
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT COALESCE(jsonb_object_agg(item.key, item.value), '{}'::jsonb)
            FROM jsonb_each(p_document) AS item
            WHERE item.key = ANY(p_keys)
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_evidence_digest(
            p_stream_key text,
            p_sequence bigint,
            p_previous_digest text,
            p_source_table text,
            p_source_key jsonb,
            p_canonical_payload jsonb
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT encode(
                digest(
                    jsonb_build_object(
                        'stream_key', p_stream_key,
                        'sequence', p_sequence,
                        'previous_digest', p_previous_digest,
                        'source_table', p_source_table,
                        'source_key', p_source_key,
                        'canonical_payload', p_canonical_payload
                    )::text,
                    'sha256'
                ),
                'hex'
            )
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_append_evidence(
            p_stream_key text,
            p_source_table text,
            p_source_key jsonb,
            p_canonical_payload jsonb
        ) RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_previous_sequence bigint;
            v_previous_digest text;
            v_sequence bigint;
            v_digest text;
        BEGIN
            IF p_stream_key IS NULL OR p_stream_key = '' THEN
                RAISE EXCEPTION 'evidence stream key must not be empty';
            END IF;
            IF p_source_table IS NULL OR p_source_table = '' THEN
                RAISE EXCEPTION 'evidence source table must not be empty';
            END IF;
            IF p_source_key = '{}'::jsonb THEN
                RAISE EXCEPTION 'evidence source key must not be empty';
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended(p_stream_key, 0));

            IF EXISTS (
                SELECT 1
                FROM public.evidence_integrity_entries
                WHERE source_table = p_source_table
                  AND source_key = p_source_key
            ) THEN
                RAISE EXCEPTION 'evidence source already captured: %.%', p_source_table, p_source_key;
            END IF;

            SELECT e.sequence, e.digest
              INTO v_previous_sequence, v_previous_digest
              FROM public.evidence_integrity_entries AS e
             WHERE e.stream_key = p_stream_key
             ORDER BY e.sequence DESC
             LIMIT 1;

            v_sequence := COALESCE(v_previous_sequence, 0) + 1;
            v_digest := public.charteros_evidence_digest(
                p_stream_key,
                v_sequence,
                v_previous_digest,
                p_source_table,
                p_source_key,
                p_canonical_payload
            );

            INSERT INTO public.evidence_integrity_entries (
                stream_key,
                sequence,
                source_table,
                source_key,
                canonical_payload,
                previous_digest,
                digest
            ) VALUES (
                p_stream_key,
                v_sequence,
                p_source_table,
                p_source_key,
                p_canonical_payload,
                v_previous_digest,
                v_digest
            );

            RETURN v_sequence;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_capture_evidence_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_row jsonb;
            v_primary_keys text[];
            v_stream_keys text[];
            v_mutable_keys text[];
            v_source_key jsonb;
            v_stream_identity jsonb;
            v_stream_key text;
            v_payload jsonb;
        BEGIN
            v_row := to_jsonb(NEW);
            v_primary_keys := string_to_array(TG_ARGV[0], ',');
            v_stream_keys := string_to_array(TG_ARGV[1], ',');
            v_mutable_keys := CASE
                WHEN TG_NARGS < 3 OR TG_ARGV[2] = '' THEN ARRAY[]::text[]
                ELSE string_to_array(TG_ARGV[2], ',')
            END;

            v_source_key := public.charteros_jsonb_pick(v_row, v_primary_keys);
            v_stream_identity := public.charteros_jsonb_pick(v_row, v_stream_keys);
            v_stream_key := TG_TABLE_NAME || ':' || v_stream_identity::text;
            v_payload := v_row - v_mutable_keys;

            PERFORM public.charteros_append_evidence(
                v_stream_key,
                TG_TABLE_NAME,
                v_source_key,
                v_payload
            );
            RETURN NEW;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_guard_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_mutable_keys text[];
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'historical evidence in % is append-only', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            v_mutable_keys := CASE
                WHEN TG_NARGS = 0 OR TG_ARGV[0] = '' THEN ARRAY[]::text[]
                ELSE string_to_array(TG_ARGV[0], ',')
            END;

            IF (to_jsonb(OLD) - v_mutable_keys)
                IS DISTINCT FROM
               (to_jsonb(NEW) - v_mutable_keys)
            THEN
                RAISE EXCEPTION 'immutable evidence columns in % cannot be modified', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_guard_integrity_ledger()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'evidence integrity ledger is append-only'
                USING ERRCODE = 'integrity_constraint_violation';
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_validate_evidence_checkpoint()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_digest text;
        BEGIN
            SELECT e.digest
              INTO v_digest
              FROM public.evidence_integrity_entries AS e
             WHERE e.stream_key = NEW.stream_key
               AND e.sequence = NEW.through_sequence;

            IF v_digest IS NULL OR v_digest <> NEW.root_digest THEN
                RAISE EXCEPTION 'checkpoint root does not match evidence stream'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_verify_evidence_integrity(
            p_stream_key text DEFAULT NULL
        ) RETURNS TABLE (
            stream_key text,
            sequence bigint,
            source_table text,
            source_key jsonb,
            violation text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_entry record;
            v_checkpoint record;
            v_current_stream text := NULL;
            v_expected_sequence bigint := 1;
            v_expected_previous text := NULL;
            v_expected_digest text;
            v_current_row jsonb;
            v_current_payload jsonb;
            v_payload_keys text[];
            v_checkpoint_digest text;
        BEGIN
            FOR v_entry IN
                SELECT e.*
                  FROM public.evidence_integrity_entries AS e
                 WHERE p_stream_key IS NULL OR e.stream_key = p_stream_key
                 ORDER BY e.stream_key, e.sequence
            LOOP
                IF v_current_stream IS DISTINCT FROM v_entry.stream_key THEN
                    v_current_stream := v_entry.stream_key;
                    v_expected_sequence := 1;
                    v_expected_previous := NULL;
                END IF;

                IF v_entry.sequence <> v_expected_sequence THEN
                    stream_key := v_entry.stream_key;
                    sequence := v_entry.sequence;
                    source_table := v_entry.source_table;
                    source_key := v_entry.source_key;
                    violation := 'sequence_gap';
                    RETURN NEXT;
                END IF;

                IF v_entry.previous_digest IS DISTINCT FROM v_expected_previous THEN
                    stream_key := v_entry.stream_key;
                    sequence := v_entry.sequence;
                    source_table := v_entry.source_table;
                    source_key := v_entry.source_key;
                    violation := 'previous_digest_mismatch';
                    RETURN NEXT;
                END IF;

                v_expected_digest := public.charteros_evidence_digest(
                    v_entry.stream_key,
                    v_entry.sequence,
                    v_entry.previous_digest,
                    v_entry.source_table,
                    v_entry.source_key,
                    v_entry.canonical_payload
                );
                IF v_entry.digest <> v_expected_digest THEN
                    stream_key := v_entry.stream_key;
                    sequence := v_entry.sequence;
                    source_table := v_entry.source_table;
                    source_key := v_entry.source_key;
                    violation := 'digest_mismatch';
                    RETURN NEXT;
                END IF;

                BEGIN
                    EXECUTE format(
                        'SELECT to_jsonb(t) FROM public.%I AS t WHERE to_jsonb(t) @> $1 LIMIT 1',
                        v_entry.source_table
                    )
                    INTO v_current_row
                    USING v_entry.source_key;
                EXCEPTION
                    WHEN undefined_table THEN
                        v_current_row := NULL;
                END;

                IF v_current_row IS NULL THEN
                    stream_key := v_entry.stream_key;
                    sequence := v_entry.sequence;
                    source_table := v_entry.source_table;
                    source_key := v_entry.source_key;
                    violation := 'source_row_missing';
                    RETURN NEXT;
                ELSE
                    SELECT array_agg(k)
                      INTO v_payload_keys
                      FROM jsonb_object_keys(v_entry.canonical_payload) AS keys(k);
                    v_current_payload := public.charteros_jsonb_pick(
                        v_current_row,
                        COALESCE(v_payload_keys, ARRAY[]::text[])
                    );
                    IF v_current_payload IS DISTINCT FROM v_entry.canonical_payload THEN
                        stream_key := v_entry.stream_key;
                        sequence := v_entry.sequence;
                        source_table := v_entry.source_table;
                        source_key := v_entry.source_key;
                        violation := 'source_payload_mismatch';
                        RETURN NEXT;
                    END IF;
                END IF;

                v_expected_previous := v_entry.digest;
                v_expected_sequence := v_entry.sequence + 1;
            END LOOP;

            FOR v_checkpoint IN
                SELECT c.*
                  FROM public.evidence_integrity_checkpoints AS c
                 WHERE p_stream_key IS NULL OR c.stream_key = p_stream_key
                 ORDER BY c.stream_key, c.through_sequence
            LOOP
                SELECT e.digest
                  INTO v_checkpoint_digest
                  FROM public.evidence_integrity_entries AS e
                 WHERE e.stream_key = v_checkpoint.stream_key
                   AND e.sequence = v_checkpoint.through_sequence;

                IF v_checkpoint_digest IS NULL
                    OR v_checkpoint_digest <> v_checkpoint.root_digest
                THEN
                    stream_key := v_checkpoint.stream_key;
                    sequence := v_checkpoint.through_sequence;
                    source_table := 'evidence_integrity_checkpoints';
                    source_key := jsonb_build_object('id', v_checkpoint.id);
                    violation := 'checkpoint_root_mismatch';
                    RETURN NEXT;
                END IF;
            END LOOP;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_backfill_evidence_table(
            p_table_name text,
            p_primary_keys text[],
            p_stream_keys text[],
            p_mutable_keys text[],
            p_order_by text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_row jsonb;
            v_source_key jsonb;
            v_stream_identity jsonb;
            v_stream_key text;
            v_payload jsonb;
        BEGIN
            FOR v_row IN EXECUTE format(
                'SELECT to_jsonb(t) FROM public.%I AS t ORDER BY %s',
                p_table_name,
                p_order_by
            )
            LOOP
                v_source_key := public.charteros_jsonb_pick(v_row, p_primary_keys);
                v_stream_identity := public.charteros_jsonb_pick(v_row, p_stream_keys);
                v_stream_key := p_table_name || ':' || v_stream_identity::text;
                v_payload := v_row - p_mutable_keys;
                PERFORM public.charteros_append_evidence(
                    v_stream_key,
                    p_table_name,
                    v_source_key,
                    v_payload
                );
            END LOOP;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_ei_entries_append_only
        BEFORE UPDATE OR DELETE ON evidence_integrity_entries
        FOR EACH ROW EXECUTE FUNCTION charteros_guard_integrity_ledger()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ei_checkpoints_validate
        BEFORE INSERT ON evidence_integrity_checkpoints
        FOR EACH ROW EXECUTE FUNCTION charteros_validate_evidence_checkpoint()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ei_checkpoints_append_only
        BEFORE UPDATE OR DELETE ON evidence_integrity_checkpoints
        FOR EACH ROW EXECUTE FUNCTION charteros_guard_integrity_ledger()
        """
    )
    op.execute("ALTER TABLE evidence_integrity_entries ENABLE ALWAYS TRIGGER trg_ei_entries_append_only")
    op.execute(
        "ALTER TABLE evidence_integrity_checkpoints "
        "ENABLE ALWAYS TRIGGER trg_ei_checkpoints_validate"
    )
    op.execute(
        "ALTER TABLE evidence_integrity_checkpoints "
        "ENABLE ALWAYS TRIGGER trg_ei_checkpoints_append_only"
    )

    for table_name, tag, primary_keys, stream_keys, mutable_keys, order_by in _PROTECTED_TABLES:
        op.execute(
            "SELECT charteros_backfill_evidence_table("
            f"'{table_name}', "
            f"{_text_array(primary_keys)}, "
            f"{_text_array(stream_keys)}, "
            f"{_text_array(mutable_keys)}, "
            f"'{order_by.replace(chr(39), chr(39) * 2)}'"
            ")"
        )
        mutable_argument = ",".join(mutable_keys).replace("'", "''")
        primary_argument = ",".join(primary_keys).replace("'", "''")
        stream_argument = ",".join(stream_keys).replace("'", "''")
        guard_name = f"trg_ei_{tag}_guard"
        capture_name = f"trg_ei_{tag}_capture"
        op.execute(
            f"""
            CREATE TRIGGER {guard_name}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION charteros_guard_evidence_mutation('{mutable_argument}')
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {capture_name}
            AFTER INSERT ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION charteros_capture_evidence_insert(
                '{primary_argument}',
                '{stream_argument}',
                '{mutable_argument}'
            )
            """
        )
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {guard_name}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {capture_name}")

    op.execute("DROP FUNCTION charteros_backfill_evidence_table(text, text[], text[], text[], text)")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION charteros_apply_runtime_evidence_privileges(
            p_role name
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_table text;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = p_role::text) THEN
                RAISE EXCEPTION 'database role % does not exist', p_role;
            END IF;

            EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', p_role);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
                p_role
            );

            FOREACH v_table IN ARRAY ARRAY[
                'outbox_events',
                'decision_evidence_snapshots',
                'fx_rates',
                'fx_locks',
                'fx_lock_conversions',
                'procurement_approvals',
                'contracts',
                'disruption_proposals',
                'disruption_commercial_changes',
                'disruption_buyer_decisions',
                'financial_reconciliations',
                'operator_invoice_revisions',
                'operator_invoice_lines',
                'reconciliation_disputes',
                'reconciliation_variance_approvals',
                'tender_admin_corrections'
            ]
            LOOP
                EXECUTE format('REVOKE DELETE ON TABLE public.%I FROM %I', v_table, p_role);
            END LOOP;

            FOREACH v_table IN ARRAY ARRAY[
                'decision_evidence_snapshots',
                'fx_rates',
                'fx_lock_conversions',
                'disruption_buyer_decisions',
                'operator_invoice_lines',
                'reconciliation_disputes',
                'reconciliation_variance_approvals',
                'tender_admin_corrections'
            ]
            LOOP
                EXECUTE format('REVOKE UPDATE ON TABLE public.%I FROM %I', v_table, p_role);
            END LOOP;

            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE ON TABLE '
                'public.evidence_integrity_entries FROM %I',
                p_role
            );
            EXECUTE format(
                'REVOKE UPDATE, DELETE ON TABLE '
                'public.evidence_integrity_checkpoints FROM %I',
                p_role
            );
            EXECUTE format(
                'GRANT SELECT ON TABLE public.evidence_integrity_entries, '
                'public.evidence_integrity_checkpoints TO %I',
                p_role
            );
            EXECUTE format(
                'GRANT INSERT ON TABLE public.evidence_integrity_checkpoints TO %I',
                p_role
            );
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.charteros_verify_evidence_integrity(text) TO %I',
                p_role
            );
        END
        $$;
        """
    )

    for signature in (
        "charteros_jsonb_pick(jsonb, text[])",
        "charteros_evidence_digest(text, bigint, text, text, jsonb, jsonb)",
        "charteros_append_evidence(text, text, jsonb, jsonb)",
        "charteros_capture_evidence_insert()",
        "charteros_guard_evidence_mutation()",
        "charteros_guard_integrity_ledger()",
        "charteros_validate_evidence_checkpoint()",
        "charteros_verify_evidence_integrity(text)",
        "charteros_apply_runtime_evidence_privileges(name)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def downgrade() -> None:
    bind = op.get_bind()
    persisted = bind.execute(
        sa.text(
            "SELECT count(*) FROM evidence_integrity_entries "
            "UNION ALL SELECT count(*) FROM evidence_integrity_checkpoints"
        )
    ).scalars().all()
    if any(persisted):
        raise RuntimeError(
            "refusing to downgrade 0020 while evidence integrity history exists"
        )

    for table_name, tag, *_rest in reversed(_PROTECTED_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_ei_{tag}_capture ON {table_name}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_ei_{tag}_guard ON {table_name}")

    op.execute(
        "DROP FUNCTION IF EXISTS charteros_apply_runtime_evidence_privileges(name)"
    )
    op.execute("DROP FUNCTION IF EXISTS charteros_verify_evidence_integrity(text)")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_checkpoints_append_only "
        "ON evidence_integrity_checkpoints"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_checkpoints_validate "
        "ON evidence_integrity_checkpoints"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ei_entries_append_only "
        "ON evidence_integrity_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS charteros_validate_evidence_checkpoint()")
    op.execute("DROP FUNCTION IF EXISTS charteros_guard_integrity_ledger()")
    op.execute("DROP FUNCTION IF EXISTS charteros_guard_evidence_mutation()")
    op.execute("DROP FUNCTION IF EXISTS charteros_capture_evidence_insert()")
    op.execute(
        "DROP FUNCTION IF EXISTS charteros_append_evidence(text, text, jsonb, jsonb)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "charteros_evidence_digest(text, bigint, text, text, jsonb, jsonb)"
    )
    op.execute("DROP FUNCTION IF EXISTS charteros_jsonb_pick(jsonb, text[])")
    op.drop_table("evidence_integrity_checkpoints")
    op.drop_index("ix_evidence_integrity_source", table_name="evidence_integrity_entries")
    op.drop_table("evidence_integrity_entries")
