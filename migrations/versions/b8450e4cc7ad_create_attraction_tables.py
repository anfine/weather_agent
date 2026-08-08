"""create attraction tables

Revision ID: b8450e4cc7ad
Revises: 
Create Date: 2026-08-06 20:30:28.442032
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b8450e4cc7ad"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "attractions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("coverage", sa.String(length=32), nullable=False),
        sa.Column("weather_notice", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "attraction_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attraction_id", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["attraction_id"],
            ["attractions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_index(
        op.f("ix_attraction_aliases_attraction_id"),
        "attraction_aliases",
        ["attraction_id"],
        unique=False,
    )
    op.create_table(
        "attraction_experience_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attraction_id", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "importance > 0",
            name="ck_attraction_experience_tags_importance",
        ),
        sa.ForeignKeyConstraint(
            ["attraction_id"],
            ["attractions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_attraction_experience_tags_attraction_id"),
        "attraction_experience_tags",
        ["attraction_id"],
        unique=False,
    )
    op.create_table(
        "weather_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attraction_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("elevation_m", sa.Float(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_weather_points_latitude",
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_weather_points_longitude",
        ),
        sa.ForeignKeyConstraint(
            ["attraction_id"],
            ["attractions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_weather_points_attraction_id"),
        "weather_points",
        ["attraction_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("weather_points")
    op.drop_table("attraction_experience_tags")
    op.drop_table("attraction_aliases")
    op.drop_table("attractions")
