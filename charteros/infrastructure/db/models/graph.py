from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class GraphProjectionVersionRow(Base):
    __tablename__ = "charter_graph_projection_versions"
    __table_args__ = (
        CheckConstraint("projection_version > 0", name="ck_graph_projection_version_positive"),
        CheckConstraint(
            "status IN ('building','verified','active','retired')",
            name="ck_graph_projection_version_status",
        ),
        CheckConstraint(
            "(status = 'active' AND active_key = 'active') OR "
            "(status <> 'active' AND active_key IS NULL)",
            name="ck_graph_projection_active_key",
        ),
        CheckConstraint("event_count >= 0", name="ck_graph_projection_event_count"),
        UniqueConstraint(
            "projection_name",
            "active_key",
            name="uq_graph_projection_single_active",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_digest: Mapped[str | None] = mapped_column(String(64))
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GraphProjectionCheckpointRow(Base):
    __tablename__ = "charter_graph_projection_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_checkpoint_version",
        ),
        CheckConstraint(
            "processed_event_count >= 0",
            name="ck_graph_checkpoint_processed_count",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    processed_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_recorded_event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphAggregateCursorRow(Base):
    __tablename__ = "charter_graph_aggregate_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_cursor_version",
        ),
        CheckConstraint(
            "last_aggregate_version > 0",
            name="ck_graph_cursor_version_positive",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    last_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphNodeRow(Base):
    __tablename__ = "charter_graph_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_node_version",
        ),
        CheckConstraint("char_length(node_type) > 0", name="ck_graph_node_type"),
        CheckConstraint(
            "source_aggregate_version > 0",
            name="ck_graph_node_source_version",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(String(48), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEdgeRow(Base):
    __tablename__ = "charter_graph_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_name", "projection_version"],
            [
                "charter_graph_projection_versions.projection_name",
                "charter_graph_projection_versions.projection_version",
            ],
            ondelete="RESTRICT",
            name="fk_graph_edge_version",
        ),
        CheckConstraint("char_length(edge_type) > 0", name="ck_graph_edge_type"),
        CheckConstraint(
            "source_aggregate_version > 0",
            name="ck_graph_edge_source_version",
        ),
        Index(
            "ix_charter_graph_edges_source",
            "projection_name",
            "projection_version",
            "source_type",
            "source_id",
            "edge_type",
        ),
        Index(
            "ix_charter_graph_edges_target",
            "projection_name",
            "projection_version",
            "target_type",
            "target_id",
            "edge_type",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_type: Mapped[str] = mapped_column(String(48), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(48), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(48), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
