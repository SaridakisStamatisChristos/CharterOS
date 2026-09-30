"""API abuse and resource-exhaustion bounds.

Revision ID: 0024_api_abuse_bounds
Revises: 0023_replacement_feasibility
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0024_api_abuse_bounds"
down_revision: str | None = "0023_replacement_feasibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_IDEMPOTENCY_RESPONSE_BYTES = 262_144


def upgrade() -> None:
    op.create_table(
        "api_rate_limit_windows",
        sa.Column("budget", sa.String(length=32), nullable=False),
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "request_count > 0",
            name="ck_api_rate_limit_windows_count",
        ),
        sa.PrimaryKeyConstraint(
            "budget",
            "identity_digest",
            "window_started_at",
            name="pk_api_rate_limit_windows",
        ),
    )
    op.create_index(
        "ix_api_rate_limit_windows_started_at",
        "api_rate_limit_windows",
        ["window_started_at"],
        unique=False,
    )
    op.create_index(
        "ix_idempotency_records_created_at",
        "idempotency_records",
        ["created_at"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_idempotency_records_response_size",
        "idempotency_records",
        f"octet_length(response_body::text) <= {_MAX_IDEMPOTENCY_RESPONSE_BYTES}",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_idempotency_records_response_size",
        "idempotency_records",
        type_="check",
    )
    op.drop_index("ix_idempotency_records_created_at", table_name="idempotency_records")
    op.drop_index("ix_api_rate_limit_windows_started_at", table_name="api_rate_limit_windows")
    op.drop_table("api_rate_limit_windows")
