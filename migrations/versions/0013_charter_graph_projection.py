"""Charter Graph production projection v1

Revision ID: 0013_charter_graph_projection
Revises: 0012_transactional_outbox
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0013_charter_graph_projection"
down_revision: str | None = "0012_transactional_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charter_graph_projection_versions",
        sa.Column("projection_name", sa.String(length=64), primary_key=True),
        sa.Column("projection_version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active_key", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_digest", sa.String(length=64), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "projection_version > 0",
            name="ck_graph_projection_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('building','verified','active','retired')",
            name="ck_graph_projection_version_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND active_key = 'active') OR "
            "(status <> 'active' AND active_key IS NULL)",
            name="ck_graph_projection_active_key",
        ),
        sa.CheckConstraint(
            "event_count >= 0",
            name="ck_graph_projection_event_count",
        ),
        sa.UniqueConstraint(
            "projection_name",
            "active_key",
            name="uq_graph_projection_single_active",
        ),
    )

    op.create_table(
        "charter_graph_projection_checkpoints",
        sa.Column("projection_name", sa.String(length=64), primary_key=True),
        sa.Column("projection_version", sa.Integer(), primary_key=True),
        sa.Column("processed_event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_recorded_event_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_checkpoint_version",
        ),
        sa.CheckConstraint(
            "processed_event_count >= 0",
            name="ck_graph_checkpoint_processed_count",
        ),
    )

    op.create_table(
        "charter_graph_aggregate_cursors",
        sa.Column("projection_name", sa.String(length=64), primary_key=True),
        sa.Column("projection_version", sa.Integer(), primary_key=True),
        sa.Column("aggregate_type", sa.String(length=64), primary_key=True),
        sa.Column("aggregate_id", sa.Uuid(), primary_key=True),
        sa.Column("last_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_cursor_version",
        ),
        sa.CheckConstraint(
            "last_aggregate_version > 0",
            name="ck_graph_cursor_version_positive",
        ),
    )

    op.create_table(
        "charter_graph_nodes",
        sa.Column("projection_name", sa.String(length=64), primary_key=True),
        sa.Column("projection_version", sa.Integer(), primary_key=True),
        sa.Column("node_type", sa.String(length=48), primary_key=True),
        sa.Column("node_id", sa.Uuid(), primary_key=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("source_aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("source_aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("source_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_node_version",
        ),
        sa.CheckConstraint("char_length(node_type) > 0", name="ck_graph_node_type"),
        sa.CheckConstraint(
            "source_aggregate_version > 0",
            name="ck_graph_node_source_version",
        ),
    )

    op.create_table(
        "charter_graph_edges",
        sa.Column("projection_name", sa.String(length=64), primary_key=True),
        sa.Column("projection_version", sa.Integer(), primary_key=True),
        sa.Column("edge_type", sa.String(length=48), primary_key=True),
        sa.Column("source_type", sa.String(length=48), primary_key=True),
        sa.Column("source_id", sa.Uuid(), primary_key=True),
        sa.Column("target_type", sa.String(length=48), primary_key=True),
        sa.Column("target_id", sa.Uuid(), primary_key=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("source_aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("source_aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("source_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_edge_version",
        ),
        sa.CheckConstraint("char_length(edge_type) > 0", name="ck_graph_edge_type"),
        sa.CheckConstraint(
            "source_aggregate_version > 0",
            name="ck_graph_edge_source_version",
        ),
    )
    op.create_index(
        "ix_charter_graph_edges_source",
        "charter_graph_edges",
        [
            "projection_name",
            "projection_version",
            "source_type",
            "source_id",
            "edge_type",
        ],
    )
    op.create_index(
        "ix_charter_graph_edges_target",
        "charter_graph_edges",
        [
            "projection_name",
            "projection_version",
            "target_type",
            "target_id",
            "edge_type",
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    evidence = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM charter_graph_nodes) + "
            "(SELECT count(*) FROM charter_graph_edges) + "
            "(SELECT count(*) FROM charter_graph_aggregate_cursors) + "
            "(SELECT count(*) FROM charter_graph_projection_checkpoints) + "
            "(SELECT count(*) FROM charter_graph_projection_versions) + "
            "(SELECT count(*) FROM outbox_consumer_receipts "
            " WHERE consumer_name LIKE 'charter_graph:v%')"
        )
    ).scalar_one()
    if evidence:
        raise RuntimeError(
            "cannot downgrade PR15 while Charter Graph projection evidence exists; "
            "preserve projection, checkpoint, and consumer-receipt history"
        )

    op.drop_index("ix_charter_graph_edges_target", table_name="charter_graph_edges")
    op.drop_index("ix_charter_graph_edges_source", table_name="charter_graph_edges")
    op.drop_table("charter_graph_edges")
    op.drop_table("charter_graph_nodes")
    op.drop_table("charter_graph_aggregate_cursors")
    op.drop_table("charter_graph_projection_checkpoints")
    op.drop_table("charter_graph_projection_versions")
