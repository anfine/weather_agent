#!/usr/bin/env python3
"""把景点候选数据转换成评分程序直接读取的运行时格式。"""

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT_ROOT / "data" / "attractions_candidates.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "attractions.json"
CURATED_NAMES = {"华山"}
EXPERIENCE_IMPORTANCE = {
    "scenic_view": 5,
    "hiking": 5,
    "outdoor_visit": 3,
}


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def _candidate_to_runtime(candidate: dict[str, Any]) -> dict[str, Any]:
    experience_tags = [
        {
            "id": tag,
            "name": {
                "scenic_view": "观景",
                "hiking": "徒步",
                "outdoor_visit": "户外游览",
            }[tag],
            "importance": importance,
        }
        for tag, importance in EXPERIENCE_IMPORTANCE.items()
        if tag in candidate["tags"]
    ]
    descriptive_tags = [
        tag for tag in candidate["tags"] if tag not in EXPERIENCE_IMPORTANCE
    ]

    is_regional_reference = (
        candidate["kind"] == "region"
        or candidate["review_status"] == "needs_review"
    )
    coverage = (
        "regional_reference" if is_regional_reference else "representative_point"
    )
    notice = (
        "地点范围或高差较大，评分只代表当前默认采样点，请将结果视为区域级参考。"
        if is_regional_reference
        else "评分使用一个代表性天气采样点。"
    )

    point_id = "default"
    return {
        "id": candidate["id"],
        "name": candidate["name"],
        "aliases": candidate.get("aliases", []),
        "status": "prototype",
        "kind": candidate["kind"],
        "timezone": "Asia/Shanghai",
        "descriptive_tags": descriptive_tags,
        "experience_tags": experience_tags,
        "coverage": coverage,
        "weather_notice": notice,
        "weather_points": [
            {
                "id": point_id,
                "name": f"{candidate['name']}默认天气采样点",
                "purpose": candidate["point_type"],
                "latitude": candidate["latitude"],
                "longitude": candidate["longitude"],
                "elevation_m": candidate["elevation_m"],
                "coordinate_system": candidate["coordinate_system"],
                "coordinate_source": candidate["coordinate_source"],
                "elevation_source": candidate["elevation_source"],
            }
        ],
        "default_weather_point_id": point_id,
        "data_notes": [notice, *candidate.get("issues", [])],
        "sources": [
            {
                "field": "weather_points.default",
                "title": "54个景点.xlsx",
                "row": candidate["raw_source"]["row"],
                "coordinate_system": candidate["raw_source"][
                    "coordinate_system"
                ],
            }
        ],
    }


def build_runtime_dataset(
    candidates_payload: dict[str, Any],
    existing_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    curated = {
        attraction["name"]: attraction
        for attraction in (existing_payload or {}).get("attractions", [])
        if attraction.get("name") in CURATED_NAMES
    }

    attractions = []
    for candidate in candidates_payload["destinations"]:
        attractions.append(
            curated.get(candidate["name"])
            or _candidate_to_runtime(candidate)
        )

    return {
        "schema_version": "1.0",
        "dataset_version": "v0.2-single-point",
        "attractions": attractions,
    }


def write_runtime_dataset(
    candidates_path: str | Path = DEFAULT_CANDIDATES,
    output_path: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    candidates_payload = _load_json(candidates_path)
    destination = Path(output_path)
    existing_payload = _load_json(destination) if destination.exists() else None
    payload = build_runtime_dataset(candidates_payload, existing_payload)
    with destination.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="生成评分程序使用的单采样点景点库",
    )
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    payload = write_runtime_dataset(args.candidates, args.output)
    print(json.dumps({"total": len(payload["attractions"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
