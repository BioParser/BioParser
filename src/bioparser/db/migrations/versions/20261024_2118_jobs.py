"""Create the jobs table.

Revision ID: 20261024_2118
Revises:
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_2118"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The pipeline statuses are the type of this column. Parser names, extractor
# names, and error codes grow as features are added, so they stay text and the
# application checks them. Which artifact refs go with which status stays there too.
_STATUSES = ("queued", "parsing", "parsed", "extracting", "done", "failed")
_PATH_LENGTH = 512


def upgrade() -> None:
    """Create the table that can hold one JobState."""
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parser", sa.Text(), nullable=False),
        sa.Column("extractor", sa.Text(), nullable=False),
        sa.Column("pdf_ref", sa.String(_PATH_LENGTH), nullable=False),
        sa.Column("parse_result_ref", sa.String(_PATH_LENGTH), nullable=True),
        sa.Column("extract_result_ref", sa.String(_PATH_LENGTH), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("parsing_claims", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("extracting_claims", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("schema_version = 1", name="jobs_schema_version_check"),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(status) for status in _STATUSES)})",
            name="jobs_status_check",
        ),
        sa.CheckConstraint("pdf_ref <> ''", name="jobs_pdf_ref_check"),
        sa.CheckConstraint(
            "parse_result_ref IS NULL OR parse_result_ref <> ''",
            name="jobs_parse_result_ref_check",
        ),
        sa.CheckConstraint(
            "extract_result_ref IS NULL OR extract_result_ref <> ''",
            name="jobs_extract_result_ref_check",
        ),
        sa.CheckConstraint("parsing_claims >= 0", name="jobs_parsing_claims_check"),
        sa.CheckConstraint("extracting_claims >= 0", name="jobs_extracting_claims_check"),
    )


def downgrade() -> None:
    """Drop the jobs table."""
    op.drop_table("jobs")
