from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class DataGovernanceLegalHoldRow(Base):
    __tablename__ = "data_governance_legal_holds"
    __table_args__ = (
        CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_legal_holds_tenant_kind",
        ),
        CheckConstraint(
            "status IN ('active','released')",
            name="ck_data_governance_legal_holds_status",
        ),
        CheckConstraint(
            "char_length(created_by_digest) = 64",
            name="ck_data_governance_legal_holds_creator_digest",
        ),
        CheckConstraint(
            "released_by_digest IS NULL OR char_length(released_by_digest) = 64",
            name="ck_data_governance_legal_holds_releaser_digest",
        ),
        CheckConstraint(
            "(status = 'active' AND released_at IS NULL AND released_by_digest IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'released' AND released_at IS NOT NULL AND released_by_digest IS NOT NULL "
            "AND release_reason IS NOT NULL)",
            name="ck_data_governance_legal_holds_lifecycle",
        ),
        Index(
            "uq_data_governance_active_hold",
            "tenant_kind",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_data_governance_legal_holds_tenant_time",
            "tenant_kind",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_digest: Mapped[str | None] = mapped_column(String(64))
    release_reason: Mapped[str | None] = mapped_column(String(1000))


class DataGovernanceLifecycleOperationRow(Base):
    __tablename__ = "data_governance_lifecycle_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_kind",
            "tenant_id",
            "operation",
            "request_key_digest",
            name="uq_data_governance_lifecycle_request",
        ),
        CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_lifecycle_tenant_kind",
        ),
        CheckConstraint(
            "operation IN ('closure','erasure')",
            name="ck_data_governance_lifecycle_operation",
        ),
        CheckConstraint(
            "status IN ('completed','blocked')",
            name="ck_data_governance_lifecycle_status",
        ),
        CheckConstraint(
            "char_length(request_key_digest) = 64 AND char_length(request_hash) = 64",
            name="ck_data_governance_lifecycle_request_digests",
        ),
        CheckConstraint(
            "char_length(actor_subject_digest) = 64",
            name="ck_data_governance_lifecycle_actor_digest",
        ),
        CheckConstraint(
            "completed_at >= requested_at",
            name="ck_data_governance_lifecycle_completion_time",
        ),
        Index(
            "ix_data_governance_lifecycle_tenant_time",
            "tenant_kind",
            "tenant_id",
            "requested_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_subject_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataGovernanceEventRow(Base):
    __tablename__ = "data_governance_events"
    __table_args__ = (
        CheckConstraint(
            "tenant_kind IN ('buyer','operator')",
            name="ck_data_governance_events_tenant_kind",
        ),
        CheckConstraint(
            "char_length(event_type) > 0",
            name="ck_data_governance_events_event_type",
        ),
        CheckConstraint(
            "char_length(actor_subject_digest) = 64",
            name="ck_data_governance_events_actor_digest",
        ),
        Index(
            "ix_data_governance_events_tenant_time",
            "tenant_kind",
            "tenant_id",
            "recorded_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    related_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    actor_subject_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
