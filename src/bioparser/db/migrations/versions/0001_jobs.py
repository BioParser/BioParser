"""Create the jobs table.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The six JobState statuses. Pairing rules for artifact refs stay in the application.
_STATUSES = ("queued", "parsing", "parsed", "extracting", "done", "failed")


def upgrade() -> None:
    """Create the table that can hold one JobState."""
    status_list = ", ".join(f"'{status}'" for status in _STATUSES)
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parser", sa.Text(), nullable=False),
        sa.Column("extractor", sa.Text(), nullable=False),
        sa.Column("pdf_ref", sa.Text(), nullable=False),
        sa.Column("parse_result_ref", sa.Text(), nullable=True),
        sa.Column("extract_result_ref", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "claim_counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(f"status IN ({status_list})", name="jobs_status_check"),
    )


def downgrade() -> None:
    """Drop the jobs table."""
    op.drop_table("jobs")
