import logging
import os
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from attraction_cache import AttractionCache
from database import SessionLocal
from models import Attraction, AttractionAlias
from redis_client import redis_client


logger = logging.getLogger(__name__)

# 正常景点：缓存 6 小时
ATTRACTION_CACHE_TTL_SECONDS = int(
    os.getenv("ATTRACTION_CACHE_TTL_SECONDS", "21600")
)
# 查不到：缓存 5 分钟
ATTRACTION_NOT_FOUND_CACHE_TTL_SECONDS = int(
    os.getenv("ATTRACTION_NOT_FOUND_CACHE_TTL_SECONDS", "300")
)

attraction_cache = AttractionCache(redis_client)


class AmbiguousAttractionError(ValueError):
    """景点名称对应多个不同地点。"""

    def __init__(self, query: str, candidates: list[dict[str, str | None]]) -> None:
        self.query = query
        self.candidates = candidates
        locations = "、".join(
            "".join(
                value or ""
                for value in (
                    candidate["province"],
                    candidate["city"],
                    candidate["district"],
                )
            )
            for candidate in candidates
        )
        super().__init__(f"景点名称 {query} 不唯一，请补充地区：{locations}")


def _with_children(statement):
    return statement.options(
        selectinload(Attraction.aliases),
        selectinload(Attraction.weather_points),
        selectinload(Attraction.experience_tags),
    )


def find_attraction(session: Session, query: str) -> Attraction | None:
    """按 ID、正式名称或别名查询，并预加载评分所需的子数据。"""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        raise ValueError("景点查询不能为空")

    identity_statement = _with_children(
        select(Attraction)
        .outerjoin(AttractionAlias)
        .where(
            or_(
                func.lower(Attraction.id) == normalized_query,
                func.lower(AttractionAlias.alias) == normalized_query,
            )
        )
    )
    identity_match = (
        session.scalars(identity_statement).unique().one_or_none()
    )
    if identity_match is not None:
        return identity_match

    name_statement = _with_children(
        select(Attraction)
        .where(func.lower(Attraction.name) == normalized_query)
        .order_by(
            Attraction.province,
            Attraction.city,
            Attraction.district,
            Attraction.id,
        )
    )
    name_matches = list(session.scalars(name_statement).unique())
    if not name_matches:
        return None
    if len(name_matches) == 1:
        return name_matches[0]
    raise AmbiguousAttractionError(
        query,
        [
            {
                "id": attraction.id,
                "name": attraction.name,
                "province": attraction.province,
                "city": attraction.city,
                "district": attraction.district,
            }
            for attraction in name_matches
        ],
    )


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
        "classification_status": attraction.classification_status,
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
    """优先从 Redis 读取景点，未命中时回源 MySQL。"""
    cache_available = True

    try:
        cached_result = attraction_cache.get(query)
    except RedisError as error:
        cache_available = False
        cached_result = None
        logger.warning("读取景点缓存失败，将回退 MySQL：%s", error)

    if cached_result is not None:
        if cached_result["status"] == "not_found":
            raise ValueError(f"找不到景点：{query}")

        return cached_result["attraction"]

    with SessionLocal() as session:
        attraction = find_attraction(session, query)

        if attraction is None:
            if cache_available:
                try:
                    attraction_cache.set_not_found(
                        query,
                        ttl_seconds=(
                            ATTRACTION_NOT_FOUND_CACHE_TTL_SECONDS
                        ),
                    )
                except RedisError as error:
                    logger.warning("写入景点负缓存失败：%s", error)

            raise ValueError(f"找不到景点：{query}")

        payload = attraction_to_payload(attraction)

    if cache_available:
        try:
            attraction_cache.set_found(
                query,
                payload,
                ttl_seconds=ATTRACTION_CACHE_TTL_SECONDS,
            )
        except RedisError as error:
            logger.warning("写入景点缓存失败：%s", error)

    return payload
