"""Merge the document title and failed-stage migrations.

Revision ID: 20261128_150000
Revises: 20261124_210800, 20261123_170000
Create Date: 2026-11-28 15:00:00
"""

from collections.abc import Sequence

revision: str = "20261128_150000"
down_revision: str | Sequence[str] | None = ("20261124_210800", "20261123_170000")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent migrations supply the schema changes."""


def downgrade() -> None:
    """Keep both parent revisions when removing the merge point."""
