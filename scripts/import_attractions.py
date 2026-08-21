#!/usr/bin/env python3
"""读取全国景区 Excel，并幂等同步到 MySQL。"""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata
import zipfile
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import session_scope
from models import Attraction, AttractionExperienceTag, WeatherPoint
from attraction_cache import AttractionCache
from redis_client import redis_client

attraction_cache = AttractionCache(redis_client)

DEFAULT_INPUT = PROJECT_ROOT / "data" / "01-23年全国景区数据.xlsx"
SOURCE_NAME = "01-23年全国景区数据.xlsx"
XML_NAMESPACE = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
}
EXPECTED_COLUMNS = {
    "A": ("name", "景区名称"),
    "B": ("grade", "等级"),
    "C": ("province", "所属省份"),
    "D": ("city", "所属城市"),
    "E": ("district", "所属区县"),
    "F": ("address", "地址"),
    "G": ("grade_assessed_at", "当前等级评定时间"),
    "H": ("source_note", "相关文件发布时间"),
    "I": ("gcj02_longitude", "坐标(GCJ02)Lng"),
    "J": ("gcj02_latitude", "坐标(GCJ02)Lat"),
    "K": ("bd09_longitude", "坐标(BD09)Lng"),
    "L": ("bd09_latitude", "坐标(BD09)Lat"),
    "M": ("longitude", "坐标(WGS84)Lng"),
    "N": ("latitude", "坐标(WGS84)Lat"),
}
GENERIC_WEATHER_NOTICE = (
    "该景点尚未完成活动分类，当前仅提供通用户外天气评价。"
)


@dataclass(frozen=True)
class AttractionSource:
    """一条经过清洗、可用于同步的全国景区记录。"""

    id: str
    business_key: str
    name: str
    grade: str | None
    province: str
    city: str | None
    district: str | None
    address: str
    grade_assessed_at: date | None
    source_published_at: date | None
    source_note: str | None
    longitude: float
    latitude: float
    source_file: str
    source_row: int


def normalize_business_text(value: str) -> str:
    """标准化参与业务键的文本。"""
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(normalized.split()).casefold()


def build_attraction_business_key(
    *,
    province: str,
    name: str,
    address: str,
) -> str:
    """生成不受等级和行政区补全影响的业务唯一键。"""
    values = [
        normalize_business_text(province),
        normalize_business_text(name),
        normalize_business_text(address),
    ]
    if any(not value for value in values):
        raise ValueError("景点省份、名称和地址不能为空")
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def build_attraction_id(
    *,
    province: str,
    name: str,
    address: str,
) -> str:
    """根据业务键确定性生成稳定景点 ID。"""
    business_key = build_attraction_business_key(
        province=province,
        name=name,
        address=address,
    )
    digest = hashlib.sha256(business_key.encode("utf-8")).hexdigest()[:24]
    return f"cn-scenic-{digest}"


def _column_name(cell_reference: str) -> str:
    match = re.match(r"[A-Z]+", cell_reference)
    if not match:
        raise ValueError(f"无效单元格引用：{cell_reference}")
    return match.group(0)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(
            node.text or ""
            for node in item.iterfind(".//x:t", XML_NAMESPACE)
        )
        for item in root.findall("x:si", XML_NAMESPACE)
    ]


def _cell_text(cell, shared_strings: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(
            node.text or ""
            for node in cell.iterfind(".//x:t", XML_NAMESPACE)
        )
    value_node = cell.find("x:v", XML_NAMESPACE)
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell.get("t") == "s":
        return shared_strings[int(value)]
    return value


def read_xlsx_rows(path: str | Path = DEFAULT_INPUT) -> list[dict[str, str]]:
    """使用标准库读取全国景区表的 14 个业务列。"""
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_root = ElementTree.fromstring(
            archive.read("xl/worksheets/sheet1.xml")
        )

    raw_rows: list[dict[str, str]] = []
    for row_node in sheet_root.findall(
        ".//x:sheetData/x:row",
        XML_NAMESPACE,
    ):
        values: dict[str, str] = {}
        for cell in row_node.findall("x:c", XML_NAMESPACE):
            column = _column_name(cell.attrib["r"])
            if column in EXPECTED_COLUMNS:
                values[column] = _cell_text(cell, shared_strings).strip()
        if values:
            values["row_number"] = row_node.attrib["r"]
            raw_rows.append(values)

    if not raw_rows:
        raise ValueError("全国景区表为空")

    header = raw_rows[0]
    for column, (_, expected_header) in EXPECTED_COLUMNS.items():
        if header.get(column) != expected_header:
            raise ValueError(
                f"第 {column} 列表头应为 {expected_header}，"
                f"实际为 {header.get(column)!r}"
            )

    rows: list[dict[str, str]] = []
    for raw_row in raw_rows[1:]:
        row = {
            field: raw_row.get(column, "").strip()
            for column, (field, _) in EXPECTED_COLUMNS.items()
        }
        row["row_number"] = raw_row["row_number"]
        if any(row[field] for field, _ in EXPECTED_COLUMNS.values()):
            rows.append(row)
    return rows


def _required_text(row: dict[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value or value == "-":
        raise ValueError(f"{field} 不能为空")
    return value


def _optional_text(value: str) -> str | None:
    cleaned = value.strip()
    return None if not cleaned or cleaned == "-" else cleaned


def _coordinate(
    row: dict[str, str],
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(row.get(field, ""))
    except ValueError as error:
        raise ValueError(f"{field} 必须是数字") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} 超出范围")
    return value


def _parse_date(value: str) -> date | None:
    cleaned = value.strip()
    if not cleaned or cleaned == "-":
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return date(1899, 12, 30) + timedelta(days=int(float(cleaned)))
    match = re.search(
        r"(?P<year>\d{4})[-年/.](?P<month>\d{1,2})"
        r"(?:[-月/.](?P<day>\d{1,2}))?",
        cleaned,
    )
    if not match:
        return None
    return date(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day") or 1),
    )


def parse_attraction_source(
    row: dict[str, str],
    *,
    source_file: str = SOURCE_NAME,
) -> AttractionSource:
    """把一行 Excel 文本解析成经过校验的景区记录。"""
    name = _required_text(row, "name")
    province = _required_text(row, "province")
    address = _required_text(row, "address")
    grade = _optional_text(row.get("grade", ""))
    if grade is not None and not re.fullmatch(r"[1-5]A", grade):
        raise ValueError(f"grade 格式无效：{grade}")
    business_key = build_attraction_business_key(
        province=province,
        name=name,
        address=address,
    )
    source_note = _optional_text(row.get("source_note", ""))
    return AttractionSource(
        id=build_attraction_id(
            province=province,
            name=name,
            address=address,
        ),
        business_key=business_key,
        name=name,
        grade=grade,
        province=province,
        city=_optional_text(row.get("city", "")),
        district=_optional_text(row.get("district", "")),
        address=address,
        grade_assessed_at=_parse_date(row.get("grade_assessed_at", "")),
        source_published_at=_parse_date(source_note or ""),
        source_note=source_note,
        longitude=_coordinate(row, "longitude", -180, 180),
        latitude=_coordinate(row, "latitude", -90, 90),
        source_file=source_file,
        source_row=int(row["row_number"]),
    )


def _content_signature(source: AttractionSource) -> tuple[Any, ...]:
    return (
        source.name,
        source.grade,
        source.province,
        source.city,
        source.district,
        source.address,
        source.grade_assessed_at,
        source.source_published_at,
        source.source_note,
        source.longitude,
        source.latitude,
    )


def _newness(source: AttractionSource) -> tuple[date, date, int, int]:
    grade_rank = int(source.grade[0]) if source.grade else 0
    return (
        source.grade_assessed_at or date.min,
        source.source_published_at or date.min,
        grade_rank,
        source.source_row,
    )


def load_nationwide_sources(
    path: str | Path = DEFAULT_INPUT,
) -> tuple[list[AttractionSource], dict[str, Any]]:
    """读取、校验并按业务键合并全国景区记录。"""
    rows = read_xlsx_rows(path)
    valid: list[AttractionSource] = []
    invalid_rows: list[dict[str, Any]] = []
    for row in rows:
        try:
            valid.append(
                parse_attraction_source(
                    row,
                    source_file=Path(path).name,
                )
            )
        except (TypeError, ValueError) as error:
            invalid_rows.append(
                {
                    "row": int(row["row_number"]),
                    "reason": str(error),
                }
            )

    grouped: dict[str, list[AttractionSource]] = defaultdict(list)
    for source in valid:
        grouped[source.business_key].append(source)

    selected: list[AttractionSource] = []
    conflicts: list[dict[str, Any]] = []
    duplicate_rows = 0
    for sources in grouped.values():
        chosen = max(sources, key=_newness)
        selected.append(chosen)
        if len(sources) == 1:
            continue
        duplicate_rows += len(sources) - 1
        signatures = {_content_signature(source) for source in sources}
        if len(signatures) > 1:
            conflicts.append(
                {
                    "business_key": chosen.business_key,
                    "name": chosen.name,
                    "rows": sorted(source.source_row for source in sources),
                    "selected_row": chosen.source_row,
                }
            )

    selected.sort(key=lambda source: source.id)
    if len({source.id for source in selected}) != len(selected):
        raise ValueError("景点 ID 摘要发生碰撞")

    return selected, {
        "raw_rows": len(rows),
        "valid_rows": len(valid),
        "business_keys": len(selected),
        "duplicate_rows": duplicate_rows,
        "conflict_groups": len(conflicts),
        "invalid_rows": invalid_rows,
        "conflicts": conflicts,
    }


def _load_existing(
    session: Session,
    sources: list[AttractionSource],
) -> tuple[
    dict[str, Attraction],
    dict[str, Attraction],
    dict[str, list[Attraction]],
]:
    options = (
        selectinload(Attraction.weather_points),
        selectinload(Attraction.experience_tags),
    )
    dataset_statement = (
        select(Attraction)
        .where(Attraction.source_file == SOURCE_NAME)
        .options(*options)
    )
    dataset_attractions = list(session.scalars(dataset_statement))
    by_business_key = {
        build_attraction_business_key(
            province=attraction.province or "",
            name=attraction.name,
            address=attraction.address or "",
        ): attraction
        for attraction in dataset_attractions
        if attraction.province and attraction.address
    }

    source_ids = [
        source.id
        for source in sources
        if source.business_key not in by_business_key
    ]
    by_id = {attraction.id: attraction for attraction in dataset_attractions}
    batch_size = 1000
    for offset in range(0, len(source_ids), batch_size):
        batch = source_ids[offset : offset + batch_size]
        statement = (
            select(Attraction)
            .where(Attraction.id.in_(batch))
            .options(*options)
        )
        by_id.update(
            {
                attraction.id: attraction
                for attraction in session.scalars(statement)
            }
        )

    legacy_statement = (
        select(Attraction)
        .where(Attraction.source_file.is_(None))
        .options(*options)
    )
    legacy_by_name: dict[str, list[Attraction]] = defaultdict(list)
    for attraction in session.scalars(legacy_statement):
        legacy_by_name[attraction.name].append(attraction)
    return by_id, by_business_key, legacy_by_name


def _set_if_changed(
    attraction: Attraction,
    field: str,
    value: Any,
) -> bool:
    if getattr(attraction, field) == value:
        return False
    setattr(attraction, field, value)
    return True


def _apply_source(attraction: Attraction, source: AttractionSource) -> bool:
    changed = False
    values = {
        "name": source.name,
        "grade": source.grade,
        "province": source.province,
        "city": source.city,
        "district": source.district,
        "address": source.address,
        "grade_assessed_at": source.grade_assessed_at,
        "source_published_at": source.source_published_at,
        "source_note": source.source_note,
        "source_file": source.source_file,
        "source_row": source.source_row,
    }
    is_classified = attraction.classification_status == "classified"
    if not is_classified:
        values.update(
            {
                "classification_status": "pending",
                "coverage": "representative_point",
                "weather_notice": GENERIC_WEATHER_NOTICE,
            }
        )
    for field, value in values.items():
        changed |= _set_if_changed(attraction, field, value)

    if is_classified:
        return changed

    default_points = [
        point for point in attraction.weather_points if point.is_default
    ]
    if len(default_points) > 1:
        raise ValueError(f"景点 {source.name} 存在多个默认天气点")
    if default_points:
        point = default_points[0]
    else:
        point = WeatherPoint(is_default=True)
        attraction.weather_points.append(point)
        changed = True
    point_values = {
        "name": f"{source.name}默认天气点",
        "longitude": source.longitude,
        "latitude": source.latitude,
        "elevation_m": None,
    }
    for field, value in point_values.items():
        if getattr(point, field) != value:
            setattr(point, field, value)
            changed = True

    if not attraction.experience_tags:
        attraction.experience_tags.append(
            AttractionExperienceTag(
                tag="outdoor_visit",
                importance=3.0,
            )
        )
        changed = True
    return changed


def sync_nationwide_attractions(
    session: Session,
    sources: list[AttractionSource],
) -> dict[str, int]:
    """在调用方事务内批量创建或更新全国景区。"""
    by_id, by_business_key, legacy_by_name = _load_existing(session, sources)
    created = 0
    updated = 0
    skipped = 0
    merged_legacy = 0
    for source in sources:
        attraction = by_business_key.get(source.business_key)
        if attraction is None:
            attraction = by_id.get(source.id)
        if attraction is None:
            legacy_matches = legacy_by_name.get(source.name, [])
            if len(legacy_matches) == 1:
                attraction = legacy_matches[0]
                merged_legacy += 1
        if attraction is None:
            attraction = Attraction(
                id=source.id,
                name=source.name,
                classification_status="pending",
                coverage="representative_point",
            )
            session.add(attraction)
            created += 1
            _apply_source(attraction, source)
        elif _apply_source(attraction, source):
            updated += 1
        else:
            skipped += 1
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "merged_legacy": merged_legacy,
    }


def import_nationwide_attractions(
    path: str | Path = DEFAULT_INPUT,
) -> dict[str, Any]:
    """导入全国景区，并在事务提交后清空景点缓存。"""
    sources, parse_report = load_nationwide_sources(path)

    with session_scope() as session:
        sync_report = sync_nationwide_attractions(session, sources)

    deleted_cache_entries = attraction_cache.clear_all()

    return {
        **parse_report,
        **sync_report,
        "deleted_cache_entries": deleted_cache_entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入全国景区 Excel")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析和检查数据，不写入数据库",
    )
    args = parser.parse_args()
    if args.dry_run:
        sources, report = load_nationwide_sources(args.input)
        result = {**report, "ready_to_import": len(sources)}
    else:
        result = import_nationwide_attractions(args.input)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
