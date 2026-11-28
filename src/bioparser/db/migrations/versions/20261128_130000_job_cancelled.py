"""Let a curator cancel a job that has not finished.

Revision ID: 20261128_130000
Revises: 20261128_120000
Create Date: 2026-11-28 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261128_130000"
down_revision: str | None = "20261128_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("queued", "parsing", "parsed", "extracting", "done", "failed")
_FINISHED = ("done", "failed")


def _in(values: tuple[str, ...]) -> str:
    return f"({', '.join(repr(value) for value in values)})"


def _replace_status_checks(statuses: tuple[str, ...], finished: tuple[str, ...]) -> None:
    op.drop_constraint("jobs_status_check", "jobs", type_="check")
    op.create_check_constraint("jobs_status_check", "jobs", f"status IN {_in(statuses)}")
    op.drop_constraint("jobs_deleted_at_check", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_deleted_at_check", "jobs", f"deleted_at IS NULL OR status IN {_in(finished)}"
    )


def upgrade() -> None:
    """Add the `cancelled` status and cancelled_stage, the status a job was cancelled from.

    A cancelled job is final, so it may be deleted like `done` and `failed`. Like
    failed_stage, cancelled_stage says whether the parser artifact was kept.
    """
    _replace_status_checks((*_STATUSES, "cancelled"), (*_FINISHED, "cancelled"))
    op.add_column("jobs", sa.Column("cancelled_stage", sa.Text(), nullable=True))
    op.create_check_constraint(
        "jobs_cancelled_stage_check",
        "jobs",
        "(status = 'cancelled') = (cancelled_stage IS NOT NULL) AND ("
        "cancelled_stage IS NULL"
        " OR (cancelled_stage IN ('queued', 'parsing') AND parse_result_ref IS NULL)"
        " OR (cancelled_stage IN ('parsed', 'extracting') AND parse_result_ref IS NOT NULL)"
        ")",
    )


def downgrade() -> None:
    """Turn cancelled jobs into failed ones, then drop `cancelled`.

    An older app rejects the `cancelled` status, and one such row makes every job list
    fail. A cancelled job becomes `failed` with `internal_error` at the stage it was
    cancelled from. Downgrade this revision before reverting the app.
    """
    op.execute(
        """
        UPDATE jobs
        SET status = 'failed',
            error_code = 'internal_error',
            failed_stage = cancelled_stage,
            cancelled_stage = NULL
        WHERE status = 'cancelled'
        """
    )
    op.drop_constraint("jobs_cancelled_stage_check", "jobs", type_="check")
    op.drop_column("jobs", "cancelled_stage")
    _replace_status_checks(_STATUSES, _FINISHED)
