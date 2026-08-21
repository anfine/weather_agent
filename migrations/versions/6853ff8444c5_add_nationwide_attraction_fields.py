"""add nationwide attraction fields

Revision ID: 6853ff8444c5
Revises: c91107e2af61
Create Date: 2026-08-19 15:51:59.364565
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "6853ff8444c5"
down_revision: str | None = "c91107e2af61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "attractions",
        sa.Column("grade", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("province", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("city", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("district", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("address", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("grade_assessed_at", sa.Date(), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("source_published_at", sa.Date(), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("source_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("source_file", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "attractions",
        sa.Column("source_row", sa.Integer(), nullable=True),
    )
    op.drop_index("name", table_name="attractions")
    op.create_index(
        "ix_attractions_city",
        "attractions",
        ["city"],
        unique=False,
    )
    op.create_index(
        "ix_attractions_name",
        "attractions",
        ["name"],
        unique=False,
    )
    op.create_index(
        "ix_attractions_province",
        "attractions",
        ["province"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index("ix_attractions_province", table_name="attractions")
    op.drop_index("ix_attractions_name", table_name="attractions")
    op.drop_index("ix_attractions_city", table_name="attractions")
    op.create_index("name", "attractions", ["name"], unique=True)
    op.drop_column("attractions", "source_row")
    op.drop_column("attractions", "source_file")
    op.drop_column("attractions", "source_note")
    op.drop_column("attractions", "source_published_at")
    op.drop_column("attractions", "grade_assessed_at")
    op.drop_column("attractions", "address")
    op.drop_column("attractions", "district")
    op.drop_column("attractions", "city")
    op.drop_column("attractions", "province")
    op.drop_column("attractions", "grade")
