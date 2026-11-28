"""Add an optional title to documents.

Revision ID: 20261124_210800
Revises: 20261122_180000
Create Date: 2026-11-24 21:08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_210800"
down_revision: str | None = "20261122_180000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add title. Existing documents receive NULL."""
    op.add_column(
        "documents",
        sa.Column("title", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove document titles."""
    op.drop_column("documents", "title")
