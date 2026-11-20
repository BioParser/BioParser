"""Point each job at its document and drop the stored PDF path.

Revision ID: 20261118_220000
Revises: 20261028_154000
Create Date: 2026-11-18 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261118_220000"
down_revision: str | None = "20261028_154000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PATH_LENGTH = 512


def upgrade() -> None:
    """Copy pdf/<checksum> onto the job, then drop that path column.

    A checksum with no document gets a row with no display name and size 0.
    A path that is not pdf/<64 hex> leaves the checksum null, and the NOT NULL
    step fails the upgrade.
    """
    op.add_column("jobs", sa.Column("document_checksum", sa.String(64), nullable=True))
    op.execute(
        """
        INSERT INTO documents (checksum, display_name, size_bytes)
        SELECT DISTINCT substring(pdf_ref FROM 5), NULL, 0
        FROM jobs
        WHERE pdf_ref ~ '^pdf/[0-9a-f]{64}$'
        ON CONFLICT (checksum) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE jobs
        SET document_checksum = substring(pdf_ref FROM 5)
        WHERE pdf_ref ~ '^pdf/[0-9a-f]{64}$'
        """
    )
    op.alter_column("jobs", "document_checksum", nullable=False)
    op.create_foreign_key(
        "jobs_document_checksum_fkey",
        "jobs",
        "documents",
        ["document_checksum"],
        ["checksum"],
    )
    # Postgres does not index a foreign key. This one serves the jobs of one document.
    op.create_index("jobs_document_checksum_idx", "jobs", ["document_checksum"])
    op.execute("UPDATE jobs SET schema_version = 2 WHERE schema_version = 1")
    op.drop_constraint("jobs_pdf_ref_check", "jobs", type_="check")
    op.drop_column("jobs", "pdf_ref")


def downgrade() -> None:
    """Restore pdf/<checksum> from the document key."""
    op.add_column("jobs", sa.Column("pdf_ref", sa.String(_PATH_LENGTH), nullable=True))
    op.execute("UPDATE jobs SET pdf_ref = 'pdf/' || document_checksum")
    op.alter_column("jobs", "pdf_ref", nullable=False)
    op.create_check_constraint("jobs_pdf_ref_check", "jobs", "pdf_ref <> ''")
    op.execute("UPDATE jobs SET schema_version = 1 WHERE schema_version = 2")
    op.drop_index("jobs_document_checksum_idx", table_name="jobs")
    op.drop_constraint("jobs_document_checksum_fkey", "jobs", type_="foreignkey")
    op.drop_column("jobs", "document_checksum")
