"""add attraction classification status

Revision ID: 3349a819d372
Revises: 6853ff8444c5
Create Date: 2026-08-19 16:00:15.954107
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "3349a819d372"
down_revision: str | None = "6853ff8444c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "attractions",
        sa.Column(
            "classification_status",
            sa.String(length=32),
            server_default="classified",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_attractions_classification_status",
        "attractions",
        "classification_status IN ('pending', 'classified')",
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(
        "ck_attractions_classification_status",
        "attractions",
        type_="check",
    )
    op.drop_column("attractions", "classification_status")
