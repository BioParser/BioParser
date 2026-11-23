"""Record the status a job had when it failed.

Revision ID: 20261123_170000
Revises: 20261122_180000
Create Date: 2026-11-23 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261123_170000"
down_revision: str | None = "20261122_180000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add failed_stage and fill it for jobs that already failed.

    A stored parser artifact means the job had reached extraction. Older rows
    do not say whether that was `parsed` or `extracting`, so they are recorded
    as `extracting`, which is what the UI used to assume. A failure with no
    parser artifact is recorded as `parsing`.
    """
    op.add_column("jobs", sa.Column("failed_stage", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE jobs
        SET failed_stage = CASE
            WHEN parse_result_ref IS NOT NULL THEN 'extracting'
            ELSE 'parsing'
        END
        WHERE status = 'failed'
        """
    )
    op.create_check_constraint(
        "jobs_failed_stage_check",
        "jobs",
        "("
        "status <> 'failed' AND failed_stage IS NULL"
        ") OR ("
        "status = 'failed' AND ("
        "(failed_stage IN ('queued', 'parsing') AND parse_result_ref IS NULL)"
        " OR (failed_stage IN ('parsed', 'extracting') AND parse_result_ref IS NOT NULL)"
        ")"
        ")",
    )


def downgrade() -> None:
    """Drop failed_stage. A failed job no longer says which status it replaced."""
    op.drop_constraint("jobs_failed_stage_check", "jobs", type_="check")
    op.drop_column("jobs", "failed_stage")
