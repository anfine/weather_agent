#!/usr/bin/env python3
"""把手工整理的百度坐标景点表转换为待审核的天气地点数据。"""

import argparse
import json
import math
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "54个景点.xlsx"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "attractions_candidates.json"
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
XML_NAMESPACE = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
}

NAME_FIXES = {
    "神农架-": "神农架",
    "沪沽湖": "泸沽湖",
}

# 这两个山峰已有比原始两位小数百度坐标更可靠的代表点。
MANUAL_OVERRIDES = {
    "华山": {
        "longitude": 110.077847,
        "latitude": 34.477799,
        "elevation_m": 2154.9,
        "point_type": "summit",
        "coordinate_source": "manual_override",
        "elevation_source": "official_survey",
        "source_url": "https://jst.sc.gov.cn/scjst/tonggao/2009/1/19/d80b2f6b24164ecf99c98682005eddde.shtml",
    },
    "黄山": {
        "longitude": 118.183333,
        "latitude": 30.166667,
        "elevation_m": 1864.8,
        "point_type": "summit",
        "coordinate_source": "manual_override",
        "elevation_source": "official_peak_elevation",
        "source_url": "https://whc.unesco.org/document/162545",
    },
}

CITY_NAMES = {
    "西安",
    "南京",
    "北京",
    "苏州",
    "大理",
    "澳门",
    "丽江",
    "腾冲",
    "喀什",
    "日喀则",
    "绍兴",
    "香格里拉",
    "塔什库尔干",
    "丹巴",
    "林芝",
    "阳朔",
    "德格",
    "都江堰",
    "拉萨",
    "敦煌",
}

REGION_NAMES = {
    "伊犁",
    "徽州",
    "阿里",
    "三江源",
    "怒江",
    "黔东南",
    "甘南",
    "山南",
    "三峡",
}

HIKING_NAMES = {
    "喀纳斯",
    "海螺沟",
    "三江源",
    "雅鲁藏布江大峡谷",
    "怒江",
    "神农架",
    "香格里拉",
    "塔什库尔干",
    "丹巴",
    "林芝",
    "稻城亚丁",
    "蜀南竹海",
    "甘南",
    "山南",
    "九寨沟",
    "长白山",
    "华山",
    "黄山",
    "四姑娘山",
}

SCENIC_NAMES = {
    "喀纳斯",
    "塔克拉玛干沙漠",
    "海螺沟",
    "三江源",
    "雅鲁藏布江大峡谷",
    "怒江",
    "德天瀑布",
    "神农架",
    "壶口瀑布",
    "香格里拉",
    "塔什库尔干",
    "丹巴",
    "林芝",
    "稻城亚丁",
    "蜀南竹海",
    "阳朔",
    "甘南",
    "婺源",
    "山南",
    "九寨沟",
    "青海湖",
    "辉腾锡勒草原",
    "长白山",
    "泸沽湖",
    "华山",
    "黄山",
    "四姑娘山",
    "三峡",
    "亚龙湾",
    "西湖",
    "凤凰古城",
    "黄姚",
}

TERRAIN_SENSITIVE_NAMES = {
    "阿里",
    "喀纳斯",
    "海螺沟",
    "三江源",
    "雅鲁藏布江大峡谷",
    "怒江",
    "神农架",
    "香格里拉",
    "塔什库尔干",
    "丹巴",
    "林芝",
    "稻城亚丁",
    "甘南",
    "山南",
    "九寨沟",
    "长白山",
    "华山",
    "黄山",
    "四姑娘山",
    "三峡",
}

DESCRIPTOR_TAGS = {
    "塔克拉玛干沙漠": ["desert"],
    "海螺沟": ["mountain", "glacier"],
    "三江源": ["plateau", "wetland"],
    "雅鲁藏布江大峡谷": ["canyon", "river"],
    "怒江": ["river", "canyon"],
    "德天瀑布": ["waterfall"],
    "神农架": ["mountain", "forest"],
    "壶口瀑布": ["waterfall", "river"],
    "西湖": ["lake", "culture"],
    "稻城亚丁": ["mountain", "plateau"],
    "蜀南竹海": ["forest"],
    "九寨沟": ["mountain", "lake"],
    "青海湖": ["lake", "plateau"],
    "辉腾锡勒草原": ["grassland"],
    "长白山": ["mountain"],
    "泸沽湖": ["lake"],
    "华山": ["mountain"],
    "黄山": ["mountain"],
    "四姑娘山": ["mountain"],
    "三峡": ["river", "canyon"],
    "亚龙湾": ["beach"],
    "凤凰古城": ["culture", "ancient_town"],
    "曲阜三孔": ["culture", "architecture"],
    "黄姚": ["culture", "ancient_town"],
    "西夏王陵": ["culture", "heritage"],
    "云冈石窟": ["culture", "heritage"],
}

X_PI = math.pi * 3000.0 / 180.0
PI = math.pi
EARTH_AXIS = 6378245.0
ECCENTRICITY_SQUARED = 0.00669342162296594323


def _column_name(cell_reference: str) -> str:
    match = re.match(r"[A-Z]+", cell_reference)
    if not match:
        raise ValueError(f"无效单元格引用：{cell_reference}")
    return match.group(0)


def read_xlsx_rows(path: str | Path) -> list[dict[str, str]]:
    """只用标准库读取当前两列表格。"""
    with zipfile.ZipFile(path) as archive:
        shared_root = ElementTree.fromstring(
            archive.read("xl/sharedStrings.xml")
        )
        shared_strings = [
            "".join(
                text.text or ""
                for text in item.findall(".//x:t", XML_NAMESPACE)
            )
            for item in shared_root.findall("x:si", XML_NAMESPACE)
        ]
        sheet_root = ElementTree.fromstring(
            archive.read("xl/worksheets/sheet1.xml")
        )

    rows: list[dict[str, str]] = []
    for row in sheet_root.findall(
        ".//x:sheetData/x:row",
        XML_NAMESPACE,
    ):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", XML_NAMESPACE):
            value_node = cell.find("x:v", XML_NAMESPACE)
            if value_node is None or value_node.text is None:
                continue
            value = value_node.text
            if cell.get("t") == "s":
                value = shared_strings[int(value)]
            values[_column_name(cell.attrib["r"])] = value
        if values:
            rows.append(values)

    if not rows or rows[0].get("A") != "city" or rows[0].get("B") != "lon,lat":
        raise ValueError("表格表头必须是 city 和 lon,lat")

    parsed_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if "A" not in row or "B" not in row:
            raise ValueError(f"第 {row_number} 行缺少名称或坐标")
        parsed_rows.append(
            {
                "row_number": str(row_number),
                "name": row["A"].strip(),
                "coordinate": row["B"].strip(),
            }
        )
    return parsed_rows


def bd09_to_gcj02(longitude: float, latitude: float) -> tuple[float, float]:
    x = longitude - 0.0065
    y = latitude - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def _transform_latitude(longitude: float, latitude: float) -> float:
    result = (
        -100.0
        + 2.0 * longitude
        + 3.0 * latitude
        + 0.2 * latitude * latitude
        + 0.1 * longitude * latitude
        + 0.2 * math.sqrt(abs(longitude))
    )
    result += (
        20.0 * math.sin(6.0 * longitude * PI)
        + 20.0 * math.sin(2.0 * longitude * PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(latitude * PI)
        + 40.0 * math.sin(latitude / 3.0 * PI)
    ) * 2.0 / 3.0
    result += (
        160.0 * math.sin(latitude / 12.0 * PI)
        + 320 * math.sin(latitude * PI / 30.0)
    ) * 2.0 / 3.0
    return result


def _transform_longitude(longitude: float, latitude: float) -> float:
    result = (
        300.0
        + longitude
        + 2.0 * latitude
        + 0.1 * longitude * longitude
        + 0.1 * longitude * latitude
        + 0.1 * math.sqrt(abs(longitude))
    )
    result += (
        20.0 * math.sin(6.0 * longitude * PI)
        + 20.0 * math.sin(2.0 * longitude * PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(longitude * PI)
        + 40.0 * math.sin(longitude / 3.0 * PI)
    ) * 2.0 / 3.0
    result += (
        150.0 * math.sin(longitude / 12.0 * PI)
        + 300.0 * math.sin(longitude / 30.0 * PI)
    ) * 2.0 / 3.0
    return result


def wgs84_to_gcj02(longitude: float, latitude: float) -> tuple[float, float]:
    relative_longitude = longitude - 105.0
    relative_latitude = latitude - 35.0
    delta_latitude = _transform_latitude(
        relative_longitude,
        relative_latitude,
    )
    delta_longitude = _transform_longitude(
        relative_longitude,
        relative_latitude,
    )
    radian_latitude = latitude / 180.0 * PI
    magic = math.sin(radian_latitude)
    magic = 1 - ECCENTRICITY_SQUARED * magic * magic
    sqrt_magic = math.sqrt(magic)
    delta_latitude = (
        delta_latitude * 180.0
    ) / ((EARTH_AXIS * (1 - ECCENTRICITY_SQUARED)) / (magic * sqrt_magic) * PI)
    delta_longitude = (
        delta_longitude * 180.0
    ) / (EARTH_AXIS / sqrt_magic * math.cos(radian_latitude) * PI)
    return longitude + delta_longitude, latitude + delta_latitude


def gcj02_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    """迭代求 GCJ-02 的近似逆变换。"""
    result_longitude = longitude
    result_latitude = latitude
    for _ in range(30):
        converted_longitude, converted_latitude = wgs84_to_gcj02(
            result_longitude,
            result_latitude,
        )
        longitude_error = longitude - converted_longitude
        latitude_error = latitude - converted_latitude
        result_longitude += longitude_error
        result_latitude += latitude_error
        if max(abs(longitude_error), abs(latitude_error)) < 1e-7:
            break
    return result_longitude, result_latitude


def bd09_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    gcj_longitude, gcj_latitude = bd09_to_gcj02(longitude, latitude)
    return gcj02_to_wgs84(gcj_longitude, gcj_latitude)


def _parse_coordinate(value: str) -> tuple[float, float]:
    try:
        raw_longitude, raw_latitude = value.split(",", maxsplit=1)
        longitude = float(raw_longitude)
        latitude = float(raw_latitude)
    except (TypeError, ValueError) as error:
        raise ValueError(f"无效坐标：{value}") from error
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError(f"坐标超出范围：{value}")
    return longitude, latitude


def _kind_for(name: str) -> str:
    if name in CITY_NAMES:
        return "city"
    if name in REGION_NAMES:
        return "region"
    return "attraction"


def _tags_for(name: str, kind: str) -> list[str]:
    tags = list(DESCRIPTOR_TAGS.get(name, []))
    if name in SCENIC_NAMES:
        tags.append("scenic_view")
    if name in HIKING_NAMES:
        tags.append("hiking")
    tags.append("outdoor_visit")
    if kind in {"city", "region"} and "culture" not in tags:
        tags.append("culture")
    return list(dict.fromkeys(tags))


def build_candidates(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    raw_coordinate_counts = Counter(row["coordinate"] for row in rows)
    candidates: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        original_name = row["name"]
        name = NAME_FIXES.get(original_name, original_name)
        raw_longitude, raw_latitude = _parse_coordinate(row["coordinate"])
        converted_longitude, converted_latitude = bd09_to_wgs84(
            raw_longitude,
            raw_latitude,
        )
        kind = _kind_for(name)
        issues: list[str] = []
        if original_name != name:
            issues.append(f"名称已规范化：{original_name} -> {name}")
        if raw_coordinate_counts[row["coordinate"]] > 1:
            issues.append(f"原始坐标与其他条目重复：{row['coordinate']}")

        candidate: dict[str, Any] = {
            "id": f"cn-destination-{index:03d}",
            "name": name,
            "aliases": [original_name] if original_name != name else [],
            "kind": kind,
            "longitude": round(converted_longitude, 6),
            "latitude": round(converted_latitude, 6),
            "coordinate_system": "WGS84_APPROX",
            "coordinate_source": "baidu_bd09_offline_conversion",
            "coordinate_accuracy_m": 1000,
            "point_type": "centroid",
            "elevation_m": None,
            "elevation_source": None,
            "tags": _tags_for(name, kind),
            "review_status": (
                "needs_review"
                if name in TERRAIN_SENSITIVE_NAMES
                else "automatic"
            ),
            "raw_source": {
                "file": DEFAULT_INPUT.name,
                "row": int(row["row_number"]),
                "longitude": raw_longitude,
                "latitude": raw_latitude,
                "coordinate_system": "BD09",
            },
            "issues": issues,
        }

        override = MANUAL_OVERRIDES.get(name)
        if override:
            candidate.update(override)
            candidate["coordinate_system"] = "WGS84"
            candidate["coordinate_accuracy_m"] = None
            candidate["review_status"] = "reviewed"
            candidate["issues"].append("已使用人工核验的代表峰覆盖原始坐标")
        candidates.append(candidate)

    return candidates


def enrich_elevations(candidates: list[dict[str, Any]]) -> None:
    pending = [
        candidate
        for candidate in candidates
        if candidate["elevation_m"] is None
    ]
    for offset in range(0, len(pending), 100):
        batch = pending[offset : offset + 100]
        response = requests.get(
            ELEVATION_API,
            params={
                "latitude": ",".join(str(item["latitude"]) for item in batch),
                "longitude": ",".join(str(item["longitude"]) for item in batch),
            },
            timeout=30,
        )
        response.raise_for_status()
        elevations = response.json().get("elevation")
        if not isinstance(elevations, list) or len(elevations) != len(batch):
            raise ValueError("Open-Meteo 返回的海拔数量与请求坐标数量不一致")
        for candidate, elevation in zip(batch, elevations):
            candidate["elevation_m"] = elevation
            candidate["elevation_source"] = "open_meteo_copernicus_dem_90m"


def import_attractions(
    input_path: str | Path,
    output_path: str | Path,
    query_elevation: bool = True,
) -> dict[str, Any]:
    rows = read_xlsx_rows(input_path)
    candidates = build_candidates(rows)
    if query_elevation:
        enrich_elevations(candidates)

    payload = {
        "schema_version": "1.0",
        "status": "candidate_dataset",
        "coordinate_conversion": {
            "source": "BD09",
            "target": "WGS84_APPROX",
            "method": "offline_approximation",
            "note": (
                "原始坐标仅保留两位小数，转换结果仍按约 1 公里定位精度处理。"
                "地形敏感地点必须人工复核。"
            ),
        },
        "elevation": {
            "automatic_source": "Open-Meteo Elevation API / Copernicus DEM 90m",
            "api": ELEVATION_API,
        },
        "summary": {
            "total": len(candidates),
            "reviewed": sum(
                item["review_status"] == "reviewed" for item in candidates
            ),
            "needs_review": sum(
                item["review_status"] == "needs_review" for item in candidates
            ),
            "automatic": sum(
                item["review_status"] == "automatic" for item in candidates
            ),
        },
        "destinations": candidates,
    }

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="转换百度景点坐标并批量补全海拔",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--skip-elevation",
        action="store_true",
        help="不调用 Open-Meteo，只生成缺少海拔的候选数据",
    )
    args = parser.parse_args()

    payload = import_attractions(
        input_path=args.input,
        output_path=args.output,
        query_elevation=not args.skip_elevation,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
