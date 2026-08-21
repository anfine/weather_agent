from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Attraction(Base):
    """可按正式名称或别名查询的景点。"""

    __tablename__ = "attractions"
    __table_args__ = (
        CheckConstraint(
            "classification_status IN ('pending', 'classified')",
            name="ck_attractions_classification_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    grade: Mapped[str | None] = mapped_column(String(8))
    province: Mapped[str | None] = mapped_column(String(32), index=True)
    city: Mapped[str | None] = mapped_column(String(64), index=True)
    district: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(512))
    grade_assessed_at: Mapped[date | None] = mapped_column(Date)
    source_published_at: Mapped[date | None] = mapped_column(Date)
    source_note: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_row: Mapped[int | None] = mapped_column(Integer)
    classification_status: Mapped[str] = mapped_column(
        String(32),
        default="classified",
        server_default="classified",
    )
    coverage: Mapped[str] = mapped_column(String(32))
    weather_notice: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    aliases: Mapped[list[AttractionAlias]] = relationship(
        back_populates="attraction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    weather_points: Mapped[list[WeatherPoint]] = relationship(
        back_populates="attraction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    experience_tags: Mapped[list[AttractionExperienceTag]] = relationship(
        back_populates="attraction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AttractionAlias(Base):
    """景点的可查询别名。"""

    __tablename__ = "attraction_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    attraction_id: Mapped[str] = mapped_column(
        ForeignKey("attractions.id", ondelete="CASCADE"),
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(128), unique=True)

    attraction: Mapped[Attraction] = relationship(back_populates="aliases")


class WeatherPoint(Base):
    """景点用于查询天气的坐标和海拔采样点。"""

    __tablename__ = "weather_points"
    __table_args__ = (
        CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_weather_points_longitude",
        ),
        CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_weather_points_latitude",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attraction_id: Mapped[str] = mapped_column(
        ForeignKey("attractions.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128))
    longitude: Mapped[float] = mapped_column(Double)
    latitude: Mapped[float] = mapped_column(Double)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )

    attraction: Mapped[Attraction] = relationship(back_populates="weather_points")


class AttractionExperienceTag(Base):
    """景点的体验类型及其在综合评分中的权重。"""

    __tablename__ = "attraction_experience_tags"
    __table_args__ = (
        CheckConstraint(
            "importance > 0",
            name="ck_attraction_experience_tags_importance",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attraction_id: Mapped[str] = mapped_column(
        ForeignKey("attractions.id", ondelete="CASCADE"),
        index=True,
    )
    tag: Mapped[str] = mapped_column(String(64))
    importance: Mapped[float] = mapped_column(Float)

    attraction: Mapped[Attraction] = relationship(
        back_populates="experience_tags"
    )
