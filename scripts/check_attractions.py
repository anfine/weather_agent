#!/usr/bin/env python3
"""检查全国景区导入结果与数据库结构。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from database import SessionLocal
from models import Attraction
from scripts.import_attractions import (
    DEFAULT_INPUT,
    build_attraction_business_key,
    load_nationwide_sources,
)


def check_attractions(path: str | Path = DEFAULT_INPUT) -> dict[str, Any]:
    """比较 Excel 有效业务键和已导入景区，不修改数据库。"""
    sources, parse_report = load_nationwide_sources(path)
    expected_keys = {source.business_key for source in sources}
    source_file = Path(path).name

    with SessionLocal() as session:
        statement = (
            select(Attraction)
            .where(Attraction.source_file == source_file)
            .options(
                selectinload(Attraction.weather_points),
                selectinload(Attraction.experience_tags),
            )
        )
        attractions = list(session.scalars(statement))
        total_attractions = session.scalar(
            select(func.count()).select_from(Attraction)
        ) or 0

    actual_keys: set[str] = set()
    invalid_database_rows: list[dict[str, Any]] = []
    default_point_errors: list[str] = []
    missing_tag_ids: list[str] = []
    for attraction in attractions:
        if not attraction.province or not attraction.address:
            invalid_database_rows.append(
                {
                    "id": attraction.id,
                    "reason": "缺少 province 或 address",
                }
            )
        else:
            actual_keys.add(
                build_attraction_business_key(
                    province=attraction.province,
                    name=attraction.name,
                    address=attraction.address,
                )
            )
        default_points = [
            point for point in attraction.weather_points if point.is_default
        ]
        if len(default_points) != 1:
            default_point_errors.append(attraction.id)
        if not attraction.experience_tags:
            missing_tag_ids.append(attraction.id)

    status_counts = Counter(
        attraction.classification_status for attraction in attractions
    )
    missing_keys = expected_keys - actual_keys
    unexpected_keys = actual_keys - expected_keys
    ok = not any(
        (
            parse_report["invalid_rows"],
            invalid_database_rows,
            default_point_errors,
            missing_tag_ids,
            missing_keys,
            unexpected_keys,
        )
    ) and len(attractions) == len(sources)

    return {
        "ok": ok,
        "raw_rows": parse_report["raw_rows"],
        "expected_business_keys": len(sources),
        "imported_business_keys": len(actual_keys),
        "total_attractions": total_attractions,
        "legacy_attractions": total_attractions - len(attractions),
        "classification_status": dict(status_counts),
        "missing_business_keys": len(missing_keys),
        "unexpected_business_keys": len(unexpected_keys),
        "invalid_database_rows": invalid_database_rows,
        "default_point_errors": default_point_errors,
        "missing_tag_ids": missing_tag_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查全国景区导入结果")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    args = parser.parse_args()
    result = check_attractions(args.input)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
