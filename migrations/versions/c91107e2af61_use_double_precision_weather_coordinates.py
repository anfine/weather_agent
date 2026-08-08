"""use double precision weather coordinates

Revision ID: c91107e2af61
Revises: b8450e4cc7ad
Create Date: 2026-08-07 17:06:40.872623
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c91107e2af61"
down_revision: str | None = "b8450e4cc7ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.alter_column(
        "weather_points",
        "longitude",
        existing_type=mysql.FLOAT(),
        type_=sa.Double(),
        existing_nullable=False,
    )
    op.alter_column(
        "weather_points",
        "latitude",
        existing_type=mysql.FLOAT(),
        type_=sa.Double(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.alter_column(
        "weather_points",
        "latitude",
        existing_type=sa.Double(),
        type_=mysql.FLOAT(),
        existing_nullable=False,
    )
    op.alter_column(
        "weather_points",
        "longitude",
        existing_type=sa.Double(),
        type_=mysql.FLOAT(),
        existing_nullable=False,
    )
