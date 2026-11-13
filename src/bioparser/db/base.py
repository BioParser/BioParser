"""The shared registry for table classes. Alembic diffs this metadata."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every table class inherits this so the migrations see one metadata."""
