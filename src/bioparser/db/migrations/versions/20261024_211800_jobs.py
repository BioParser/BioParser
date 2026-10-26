"""Create the jobs table.

Revision ID: 20261024_211800
Revises:
Create Date: 2026-10-24 21:18:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_211800"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The pipeline statuses are the type of this column. Schema version, parser
# names, extractor names, and error codes stay unconstrained apart from null,
# because those values grow as the record changes. The application checks them.
# Which artifact refs go with which status stays there too.
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
    # now() fills both stamps on insert. This keeps updated_at current on later writes.
    op.execute(
        """
        CREATE FUNCTION jobs_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER jobs_set_updated_at
        BEFORE UPDATE ON jobs
        FOR EACH ROW
        EXECUTE FUNCTION jobs_set_updated_at()
        """
    )


def downgrade() -> None:
    """Drop the jobs table."""
    op.execute("DROP TRIGGER IF EXISTS jobs_set_updated_at ON jobs")
    op.drop_table("jobs")
    op.execute("DROP FUNCTION IF EXISTS jobs_set_updated_at()")
