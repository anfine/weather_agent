#!/usr/bin/env python3
"""把景点 JSON 幂等同步到数据库。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import session_scope
from models import (
    Attraction,
    AttractionAlias,
    AttractionExperienceTag,
    WeatherPoint,
)


DEFAULT_INPUT = PROJECT_ROOT / "data" / "attractions.json"


def _required_text(item: dict[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} 必须是非空字符串")
    return value.strip()


def _number(item: dict[str, Any], field: str, context: str) -> float:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}.{field} 必须是数字")
    return float(value)


def _validate_attraction(item: dict[str, Any]) -> None:
    attraction_id = _required_text(item, "id", "attraction")
    context = f"attraction[{attraction_id}]"
    _required_text(item, "name", context)
    _required_text(item, "coverage", context)

    aliases = item.get("aliases", [])
    if not isinstance(aliases, list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in aliases
    ):
        raise ValueError(f"{context}.aliases 必须是非空字符串数组")
    normalized_aliases = [alias.strip() for alias in aliases]
    if len(set(normalized_aliases)) != len(normalized_aliases):
        raise ValueError(f"{context}.aliases 不能重复")

    weather_points = item.get("weather_points")
    if not isinstance(weather_points, list) or not weather_points:
        raise ValueError(f"{context} 至少需要一个天气采样点")
    default_point_id = _required_text(item, "default_weather_point_id", context)
    point_ids: set[str] = set()
    point_names: set[str] = set()
    for point in weather_points:
        if not isinstance(point, dict):
            raise ValueError(f"{context}.weather_points 必须包含对象")
        point_id = _required_text(point, "id", f"{context}.weather_point")
        point_name = _required_text(
            point,
            "name",
            f"{context}.weather_point[{point_id}]",
        )
        if point_id in point_ids or point_name in point_names:
            raise ValueError(f"{context} 的天气采样点 ID 和名称不能重复")
        point_ids.add(point_id)
        point_names.add(point_name)

        point_context = f"{context}.weather_point[{point_id}]"
        longitude = _number(point, "longitude", point_context)
        latitude = _number(point, "latitude", point_context)
        if not -180 <= longitude <= 180:
            raise ValueError(f"{point_context}.longitude 超出范围")
        if not -90 <= latitude <= 90:
            raise ValueError(f"{point_context}.latitude 超出范围")
        if point.get("elevation_m") is not None:
            _number(point, "elevation_m", point_context)

    if default_point_id not in point_ids:
        raise ValueError(f"{context} 的默认天气采样点不存在")

    tags = item.get("experience_tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError(f"{context} 至少需要一个体验标签")
    tag_names: set[str] = set()
    for tag in tags:
        if not isinstance(tag, dict):
            raise ValueError(f"{context}.experience_tags 必须包含对象")
        tag_name = _required_text(tag, "id", f"{context}.experience_tag")
        if tag_name in tag_names:
            raise ValueError(f"{context} 的体验标签不能重复")
        tag_names.add(tag_name)
        if _number(tag, "importance", f"{context}.experience_tag[{tag_name}]") <= 0:
            raise ValueError(f"{context}.experience_tag[{tag_name}] 的权重必须大于 0")


def load_attractions(path: str | Path = DEFAULT_INPUT) -> list[dict[str, Any]]:
    """读取并完整校验景点种子数据。"""
    with Path(path).open(encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError("种子数据必须是 JSON 对象")
    attractions = payload.get("attractions")
    if not isinstance(attractions, list) or not attractions:
        raise ValueError("种子数据缺少非空 attractions 数组")

    for attraction in attractions:
        if not isinstance(attraction, dict):
            raise ValueError("attractions 必须包含对象")
        _validate_attraction(attraction)

    ids = [str(item["id"]).strip() for item in attractions]
    names = [str(item["name"]).strip() for item in attractions]
    aliases = [
        alias.strip()
        for item in attractions
        for alias in item.get("aliases", [])
    ]
    if len(set(ids)) != len(ids):
        raise ValueError("景点 ID 不能重复")
    if len(set(names)) != len(names):
        raise ValueError("景点正式名称不能重复")
    if len(set(aliases)) != len(aliases):
        raise ValueError("景点别名不能重复")
    if set(names) & set(aliases):
        raise ValueError("景点别名不能与正式名称重复")

    return attractions


def _update_weather_point(
    point: WeatherPoint,
    source: dict[str, Any],
    default_point_id: str,
) -> None:
    point.name = str(source["name"]).strip()
    point.longitude = float(source["longitude"])
    point.latitude = float(source["latitude"])
    elevation = source.get("elevation_m")
    point.elevation_m = None if elevation is None else float(elevation)
    point.is_default = str(source["id"]).strip() == default_point_id


def _update_experience_tag(
    tag: AttractionExperienceTag,
    source: dict[str, Any],
) -> None:
    tag.tag = str(source["id"]).strip()
    tag.importance = float(source["importance"])


def seed_attractions(
    session: Session,
    sources: list[dict[str, Any]],
) -> dict[str, int]:
    """在调用方事务内把景点及其子数据同步到当前 JSON 状态。"""
    source_ids = [str(source["id"]).strip() for source in sources]
    statement = (
        select(Attraction)
        .where(Attraction.id.in_(source_ids))
        .options(
            selectinload(Attraction.aliases),
            selectinload(Attraction.weather_points),
            selectinload(Attraction.experience_tags),
        )
    )
    existing = {
        attraction.id: attraction
        for attraction in session.scalars(statement)
    }

    pending: list[tuple[Attraction, dict[str, Any]]] = []
    created = 0
    for source in sources:
        attraction_id = str(source["id"]).strip()
        attraction = existing.get(attraction_id)
        if attraction is None:
            attraction = Attraction(id=attraction_id)
            session.add(attraction)
            created += 1

        attraction.name = str(source["name"]).strip()
        attraction.coverage = str(source["coverage"]).strip()
        notice = source.get("weather_notice")
        attraction.weather_notice = (
            notice.strip() if isinstance(notice, str) and notice.strip() else None
        )

        desired_aliases = {
            alias.strip() for alias in source.get("aliases", [])
        }
        attraction.aliases[:] = [
            alias for alias in attraction.aliases if alias.alias in desired_aliases
        ]

        desired_point_names = {
            str(point["name"]).strip() for point in source["weather_points"]
        }
        attraction.weather_points[:] = [
            point
            for point in attraction.weather_points
            if point.name in desired_point_names
        ]

        desired_tags = {
            str(tag["id"]).strip() for tag in source["experience_tags"]
        }
        attraction.experience_tags[:] = [
            tag for tag in attraction.experience_tags if tag.tag in desired_tags
        ]
        pending.append((attraction, source))

    # 先删除已失效的子记录，避免移动唯一别名时与旧记录冲突。
    session.flush()

    for attraction, source in pending:
        aliases_by_name = {alias.alias: alias for alias in attraction.aliases}
        for alias_name in source.get("aliases", []):
            normalized_alias = alias_name.strip()
            if normalized_alias not in aliases_by_name:
                attraction.aliases.append(AttractionAlias(alias=normalized_alias))

        points_by_name = {
            point.name: point for point in attraction.weather_points
        }
        default_point_id = str(source["default_weather_point_id"]).strip()
        for point_source in source["weather_points"]:
            point_name = str(point_source["name"]).strip()
            point = points_by_name.get(point_name)
            if point is None:
                point = WeatherPoint(name=point_name)
                attraction.weather_points.append(point)
            _update_weather_point(point, point_source, default_point_id)

        tags_by_name = {
            tag.tag: tag for tag in attraction.experience_tags
        }
        for tag_source in source["experience_tags"]:
            tag_name = str(tag_source["id"]).strip()
            tag = tags_by_name.get(tag_name)
            if tag is None:
                tag = AttractionExperienceTag(tag=tag_name)
                attraction.experience_tags.append(tag)
            _update_experience_tag(tag, tag_source)

    return {
        "created": created,
        "updated": len(sources) - created,
        "attractions": len(sources),
        "aliases": sum(len(source.get("aliases", [])) for source in sources),
        "weather_points": sum(len(source["weather_points"]) for source in sources),
        "experience_tags": sum(len(source["experience_tags"]) for source in sources),
    }


def seed_from_file(path: str | Path = DEFAULT_INPUT) -> dict[str, int]:
    """校验完整文件，并在一个事务中同步全部景点。"""
    sources = load_attractions(path)
    with session_scope() as session:
        return seed_attractions(session, sources)


def main() -> None:
    parser = argparse.ArgumentParser(description="把景点 JSON 幂等同步到数据库")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    args = parser.parse_args()
    print(json.dumps(seed_from_file(args.input), ensure_ascii=False))


if __name__ == "__main__":
    main()
