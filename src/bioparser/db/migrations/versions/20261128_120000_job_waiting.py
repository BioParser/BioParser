"""Record which service a deferred job is waiting for, and since when.

Revision ID: 20261128_120000
Revises: 20261123_170000
Create Date: 2026-11-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261128_120000"
down_revision: str | None = "20261123_170000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add waiting_for and waiting_since, both empty on every existing row.

    They are set together. `parser` is allowed only while the parse stage owns the
    job, and `extractor` only while the extract stage owns it.
    """
    op.add_column("jobs", sa.Column("waiting_for", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "jobs_waiting_check",
        "jobs",
        "(waiting_for IS NULL) = (waiting_since IS NULL) AND ("
        "waiting_for IS NULL"
        " OR (waiting_for = 'parser' AND status IN ('queued', 'parsing'))"
        " OR (waiting_for = 'extractor' AND status IN ('parsed', 'extracting'))"
        ")",
    )


def downgrade() -> None:
    """Drop both columns. A job that was waiting shows its plain status again."""
    op.drop_constraint("jobs_waiting_check", "jobs", type_="check")
    op.drop_column("jobs", "waiting_since")
    op.drop_column("jobs", "waiting_for")
