"""repository bootstrap

Revision ID: 0001_repository_bootstrap
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

revision: str = "0001_repository_bootstrap"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish an Alembic head without introducing domain tables in PR1."""


def downgrade() -> None:
    """No schema objects are created by the bootstrap migration."""
