"""Create the documents table.

Revision ID: 20261028_154000
Revises: 20261024_211800
Create Date: 2026-10-28 15:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_154000"
down_revision: str | None = "20261024_211800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the documents table."""
    op.create_table(
        "documents",
        sa.Column("checksum", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="documents_checksum_hex_check"),
        sa.CheckConstraint("size_bytes >= 0", name="documents_size_bytes_check"),
    )


def downgrade() -> None:
    """Drop the documents table."""
    op.drop_table("documents")
