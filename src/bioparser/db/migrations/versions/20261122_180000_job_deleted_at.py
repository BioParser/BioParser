"""Soft-delete jobs: add jobs.deleted_at.

Revision ID: 20261122_180000
Revises: 20261118_220000
Create Date: 2026-11-22 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_180000"
down_revision: str | None = "20261118_220000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a null deleted_at. Only a done or failed job may have one."""
    op.add_column("jobs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "jobs_deleted_at_check",
        "jobs",
        "deleted_at IS NULL OR status IN ('done', 'failed')",
    )


def downgrade() -> None:
    """Drop deleted_at. Jobs deleted before the downgrade show in the job lists again."""
    op.drop_constraint("jobs_deleted_at_check", "jobs", type_="check")
    op.drop_column("jobs", "deleted_at")
