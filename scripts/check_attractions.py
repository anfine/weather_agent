#!/usr/bin/env python3
"""检查景点 JSON 与数据库记录数量是否一致。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    Attraction,
    AttractionAlias,
    AttractionExperienceTag,
    WeatherPoint,
)
from scripts.seed_attractions import DEFAULT_INPUT, load_attractions


def expected_counts(sources: list[dict[str, Any]]) -> dict[str, int]:
    """根据当前 JSON 动态计算预期记录数。"""
    return {
        "attractions": len(sources),
        "aliases": sum(len(source.get("aliases", [])) for source in sources),
        "weather_points": sum(
            len(source["weather_points"]) for source in sources
        ),
        "experience_tags": sum(
            len(source["experience_tags"]) for source in sources
        ),
    }


def database_counts(session: Session) -> dict[str, int]:
    """查询四张景点表的实际记录数。"""
    return {
        "attractions": session.scalar(
            select(func.count(Attraction.id))
        ) or 0,
        "aliases": session.scalar(
            select(func.count(AttractionAlias.id))
        ) or 0,
        "weather_points": session.scalar(
            select(func.count(WeatherPoint.id))
        ) or 0,
        "experience_tags": session.scalar(
            select(func.count(AttractionExperienceTag.id))
        ) or 0,
    }


def check_attractions(path: str | Path = DEFAULT_INPUT) -> dict[str, Any]:
    """比较 JSON 和数据库数量，不修改任何数据。"""
    sources = load_attractions(path)
    expected = expected_counts(sources)
    with SessionLocal() as session:
        actual = database_counts(session)

    return {
        "ok": actual == expected,
        "expected": expected,
        "actual": actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查景点导入数量")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    args = parser.parse_args()

    result = check_attractions(args.input)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
