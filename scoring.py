import json
from datetime import date, datetime, time
from pathlib import Path
from statistics import fmean
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "scoring_rules.json"
DEFAULT_ATTRACTIONS_PATH = PROJECT_ROOT / "data" / "attractions.json"


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def load_scoring_rules(
    path: str | Path = DEFAULT_RULES_PATH,
) -> dict[str, Any]:
    """读取通用体验评分规则。"""
    rules = _load_json(path)
    if not rules.get("metric_profiles") or not rules.get("experience_rules"):
        raise ValueError("评分规则缺少 metric_profiles 或 experience_rules")
    return rules


def load_attraction(
    query: str,
    path: str | Path = DEFAULT_ATTRACTIONS_PATH,
) -> dict[str, Any]:
    """根据 ID、名称或别名读取一条景点数据。"""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        raise ValueError("景点查询不能为空")

    payload = _load_json(path)
    for attraction in payload.get("attractions", []):
        candidates = [
            attraction.get("id", ""),
            attraction.get("name", ""),
            *attraction.get("aliases", []),
        ]
        if normalized_query in {
            str(candidate).strip().casefold() for candidate in candidates
        }:
            return attraction

    raise ValueError(f"找不到景点：{query}")


def _parse_target_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("target_date 必须使用 YYYY-MM-DD 格式") from error


def _parse_window(value: str, field_name: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} 必须使用 HH:MM 格式") from error


def _select_hourly_indices(
    hourly: dict[str, list[Any]],
    target_date: str,
    start_time: str,
    end_time: str,
) -> list[int]:
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise ValueError("hourly 数据缺少非空的 time 数组")

    selected_date = _parse_target_date(target_date)
    start = _parse_window(start_time, "start_time")
    end = _parse_window(end_time, "end_time")
    if start >= end:
        raise ValueError("start_time 必须早于 end_time")

    indices: list[int] = []
    for index, raw_time in enumerate(raw_times):
        try:
            timestamp = datetime.fromisoformat(str(raw_time))
        except ValueError as error:
            raise ValueError(f"hourly.time 包含无效时间：{raw_time}") from error

        if timestamp.date() == selected_date and start <= timestamp.time() < end:
            indices.append(index)

    if not indices:
        raise ValueError(
            f"{target_date} {start_time}～{end_time} 没有可用于评分的小时数据"
        )
    return indices


def _metric_values(
    hourly: dict[str, list[Any]],
    field: str,
    indices: list[int],
) -> list[float]:
    raw_values = hourly.get(field)
    if not isinstance(raw_values, list):
        raise ValueError(f"hourly 数据缺少字段：{field}")

    values: list[float] = []
    for index in indices:
        if index >= len(raw_values):
            raise ValueError(f"hourly 字段 {field} 与 time 长度不一致")
        value = raw_values[index]
        if value is not None:
            values.append(float(value))

    if not values:
        raise ValueError(f"hourly 字段 {field} 在评价时段内没有有效值")
    return values


def _aggregate(values: list[float], method: str) -> float:
    aggregators = {
        "mean": fmean,
        "max": max,
        "min": min,
        "sum": sum,
    }
    try:
        aggregator = aggregators[method]
    except KeyError as error:
        raise ValueError(f"不支持的聚合方式：{method}") from error
    return float(aggregator(values))


def _score_metric(value: float, profile: dict[str, Any]) -> float:
    for band in profile.get("bands", []):
        minimum = band.get("min")
        maximum = band.get("max")
        if (minimum is None or value >= minimum) and (
            maximum is None or value < maximum
        ):
            return float(band["score"])
    raise ValueError(f"数值 {value} 不在评分曲线 {profile} 的任何区间内")


def _rounded(value: float) -> float:
    return round(value, 1)


def score_experience(
    hourly: dict[str, list[Any]],
    target_date: str,
    experience_id: str,
    rules: dict[str, Any],
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """计算单个体验标签在指定日期和时段内的得分。"""
    try:
        experience_rule = rules["experience_rules"][experience_id]
    except KeyError as error:
        raise ValueError(f"找不到体验评分规则：{experience_id}") from error

    indices = _select_hourly_indices(
        hourly,
        target_date,
        start_time,
        end_time,
    )
    metric_results: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0.0

    for metric in experience_rule["metrics"]:
        field = metric["field"]
        aggregation = metric["aggregation"]
        profile_id = metric["profile"]
        weight = float(metric["weight"])
        if weight <= 0:
            raise ValueError(f"{experience_id}.{field} 的权重必须大于 0")

        try:
            profile = rules["metric_profiles"][profile_id]
        except KeyError as error:
            raise ValueError(f"找不到指标评分曲线：{profile_id}") from error

        value = _aggregate(
            _metric_values(hourly, field, indices),
            aggregation,
        )
        metric_score = _score_metric(value, profile)
        weighted_score += metric_score * weight
        total_weight += weight
        metric_results.append(
            {
                "field": field,
                "aggregation": aggregation,
                "value": _rounded(value),
                "unit": profile["unit"],
                "profile": profile_id,
                "weight": weight,
                "score": _rounded(metric_score),
            }
        )

    if total_weight == 0:
        raise ValueError(f"体验评分规则没有有效权重：{experience_id}")

    score = weighted_score / total_weight
    limiting_factors = [
        metric["field"]
        for metric in sorted(metric_results, key=lambda item: item["score"])
        if metric["score"] < 60
    ][:3]
    positive_factors = [
        metric["field"]
        for metric in sorted(
            metric_results,
            key=lambda item: item["score"],
            reverse=True,
        )
        if metric["score"] >= 80
    ][:3]

    return {
        "id": experience_id,
        "name": experience_rule["name"],
        "score": _rounded(score),
        "metrics": metric_results,
        "limiting_factors": limiting_factors,
        "positive_factors": positive_factors,
    }


def evaluate_attraction_weather(
    attraction: dict[str, Any],
    hourly: dict[str, list[Any]],
    target_date: str,
    rules: dict[str, Any] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """按景点体验标签计算各维度得分和综合分。"""
    active_rules = rules or load_scoring_rules()
    default_window = active_rules["default_evaluation_window"]
    selected_start = start_time or default_window["start"]
    selected_end = end_time or default_window["end"]

    experience_results: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_importance = 0.0

    for tag in attraction.get("experience_tags", []):
        experience_id = tag["id"]
        importance = float(tag["importance"])
        if importance <= 0:
            raise ValueError(f"{experience_id} 的景点重要性必须大于 0")

        result = score_experience(
            hourly=hourly,
            target_date=target_date,
            experience_id=experience_id,
            rules=active_rules,
            start_time=selected_start,
            end_time=selected_end,
        )
        result["importance"] = importance
        experience_results.append(result)
        weighted_score += result["score"] * importance
        total_importance += importance

    if total_importance == 0:
        raise ValueError(f"景点 {attraction.get('name', '')} 没有可评分的体验标签")

    return {
        "attraction_id": attraction["id"],
        "attraction_name": attraction["name"],
        "target_date": _parse_target_date(target_date).isoformat(),
        "evaluation_window": {
            "start": selected_start,
            "end": selected_end,
        },
        "rules_version": active_rules["rules_version"],
        "overall_score": _rounded(weighted_score / total_importance),
        "experiences": experience_results,
    }
