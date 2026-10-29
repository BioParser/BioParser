import sqlalchemy as sa
from alembic import op

revision = "20261028_164057"
down_revision = "20261024_211800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("file_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "file_name")
