from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from database import SessionLocal
from models import Attraction, AttractionAlias


def find_attraction(session: Session, query: str) -> Attraction | None:
    """按 ID、正式名称或别名查询，并预加载评分所需的子数据。"""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        raise ValueError("景点查询不能为空")

    statement = (
        select(Attraction)
        .outerjoin(AttractionAlias)
        .where(
            or_(
                func.lower(Attraction.id) == normalized_query,
                func.lower(Attraction.name) == normalized_query,
                func.lower(AttractionAlias.alias) == normalized_query,
            )
        )
        .options(
            selectinload(Attraction.aliases),
            selectinload(Attraction.weather_points),
            selectinload(Attraction.experience_tags),
        )
    )
    return session.scalars(statement).unique().one_or_none()


def attraction_to_payload(attraction: Attraction) -> dict[str, Any]:
    """把 ORM 对象转换成现有评分调用链使用的字典格式。"""
    aliases = sorted(attraction.aliases, key=lambda item: item.id)
    weather_points = sorted(attraction.weather_points, key=lambda item: item.id)
    experience_tags = sorted(
        attraction.experience_tags,
        key=lambda item: item.id,
    )
    default_points = [point for point in weather_points if point.is_default]
    if len(default_points) != 1:
        raise ValueError(
            f"景点 {attraction.name} 必须恰好有一个默认天气采样点"
        )

    return {
        "id": attraction.id,
        "name": attraction.name,
        "aliases": [alias.alias for alias in aliases],
        "coverage": attraction.coverage,
        "weather_notice": attraction.weather_notice,
        "experience_tags": [
            {
                "id": tag.tag,
                "importance": tag.importance,
            }
            for tag in experience_tags
        ],
        "weather_points": [
            {
                "id": point.id,
                "name": point.name,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "elevation_m": point.elevation_m,
            }
            for point in weather_points
        ],
        "default_weather_point_id": default_points[0].id,
    }


def load_attraction(query: str) -> dict[str, Any]:
    """从数据库读取一条可直接用于评分的景点数据。"""
    with SessionLocal() as session:
        attraction = find_attraction(session, query)
        if attraction is None:
            raise ValueError(f"找不到景点：{query}")
        return attraction_to_payload(attraction)
