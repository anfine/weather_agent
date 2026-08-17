from collections.abc import Callable
from datetime import date, timedelta
import json
import logging

from redis import Redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)


CACHE_KEY_PREFIX = "weather_agent:weather:v1"
logger = logging.getLogger(__name__)


def _format_coordinate(value: float) -> str:
    """将坐标标准化为缓存 key 使用的固定精度。"""
    return f"{value:.6f}"


def _format_elevation(value: float | None) -> str:
    """区分自动海拔和明确指定的海拔。"""
    if value is None:
        return "auto"
    return f"{value:.1f}"


def build_forecast_cache_key(
    *,
    latitude: float,
    longitude: float,
    elevation: float | None,
    forecast_date: date,
) -> str:
    """生成某个位置、某个自然日的预报缓存 key。"""
    return ":".join(
        [
            CACHE_KEY_PREFIX,
            "forecast",
            _format_coordinate(latitude),
            _format_coordinate(longitude),
            _format_elevation(elevation),
            forecast_date.isoformat(),
        ]
    )


def build_current_cache_key(
    *,
    latitude: float,
    longitude: float,
    elevation: float | None,
) -> str:
    """生成某个位置的当前天气缓存 key。"""
    return ":".join(
        [
            CACHE_KEY_PREFIX,
            "current",
            _format_coordinate(latitude),
            _format_coordinate(longitude),
            _format_elevation(elevation),
        ]
    )


def load_cached_forecast_days(
    cache: "WeatherCache",
    *,
    latitude: float,
    longitude: float,
    elevation: float | None,
    start_date: date,
    end_date: date,
) -> tuple[dict[date, dict], list[date]]:
    """读取日期范围内的每日缓存，并返回命中和未命中日期。"""
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")

    forecast_dates = [
        start_date + timedelta(days=offset)
        for offset in range(
            (end_date - start_date).days + 1
        )
    ]

    keys = [
        build_forecast_cache_key(
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            forecast_date=forecast_date,
        )
        for forecast_date in forecast_dates
    ]

    cached_payloads = cache.get_many(keys)

    cached_days: dict[date, dict] = {}
    missing_dates: list[date] = []

    for forecast_date, payload in zip(
        forecast_dates,
        cached_payloads,
    ):
        if payload is None:
            missing_dates.append(forecast_date)
        else:
            cached_days[forecast_date] = payload

    return cached_days, missing_dates


class WeatherCache:
    """读写序列化天气数据的 Redis 缓存。"""

    def __init__(self, client: Redis) -> None:
        self._client = client

    def get(self, key: str) -> dict | None:
        """读取缓存；不存在或内容损坏时返回 None。"""
        cached_value = self._client.get(key)

        if cached_value is None:
            return None

        try:
            payload = json.loads(cached_value)
        except json.JSONDecodeError:
            self._client.delete(key)
            return None

        if not isinstance(payload, dict):
            self._client.delete(key)
            return None

        return payload

    def get_many(
        self,
        keys: list[str],
    ) -> list[dict | None]:
        """批量读取缓存，并保持结果与 keys 的顺序一致。"""
        if not keys:
            return []

        cached_values = self._client.mget(keys)
        results: list[dict | None] = []

        for key, cached_value in zip(keys, cached_values):
            if cached_value is None:
                results.append(None)
                continue

            try:
                payload = json.loads(cached_value)
            except json.JSONDecodeError:
                self._client.delete(key)
                results.append(None)
                continue

            if not isinstance(payload, dict):
                self._client.delete(key)
                results.append(None)
                continue

            results.append(payload)

        return results

    def set(
        self,
        key: str,
        payload: dict,
        *,
        ttl_seconds: int,
    ) -> None:
        """将天气数据序列化并带 TTL 写入缓存。"""
        if ttl_seconds <= 0:
            raise ValueError("缓存 TTL 必须大于 0")

        serialized_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        self._client.set(
            key,
            serialized_payload,
            ex=ttl_seconds,
        )

    def set_many(
        self,
        payloads_by_key: dict[str, dict],
        *,
        ttl_seconds: int,
    ) -> None:
        """使用 pipeline 批量写入多份天气缓存。"""
        if ttl_seconds <= 0:
            raise ValueError("缓存 TTL 必须大于 0")

        if not payloads_by_key:
            return

        pipeline = self._client.pipeline(
            transaction=False
        )

        for key, payload in payloads_by_key.items():
            serialized_payload = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            pipeline.set(
                key,
                serialized_payload,
                ex=ttl_seconds,
            )

        pipeline.execute()


def _slice_parallel_arrays(
    data: dict[str, list],
    indexes: list[int],
) -> dict[str, list]:
    """使用相同下标截取一组长度一致的并行数组。"""
    times = data.get("time")
    if not isinstance(times, list):
        raise ValueError("天气时间序列缺少 time 数组")

    expected_length = len(times)
    selected: dict[str, list] = {}

    for field, values in data.items():
        if not isinstance(values, list):
            raise ValueError(f"天气字段 {field} 必须是数组")
        if len(values) != expected_length:
            raise ValueError(
                f"天气字段 {field} 与 time 数组长度不一致"
            )

        selected[field] = [
            values[index]
            for index in indexes
        ]

    return selected


def split_forecast_by_day(
    payload: dict,
) -> dict[date, dict]:
    """将 Open-Meteo 多日预报拆成按自然日索引的 payload。"""
    hourly = payload.get("hourly")
    daily = payload.get("daily")

    if not isinstance(hourly, dict):
        raise ValueError("天气预报缺少 hourly 对象")
    if not isinstance(daily, dict):
        raise ValueError("天气预报缺少 daily 对象")

    hourly_times = hourly.get("time")
    daily_times = daily.get("time")

    if not isinstance(hourly_times, list):
        raise ValueError("天气预报缺少 hourly.time 数组")
    if not isinstance(daily_times, list):
        raise ValueError("天气预报缺少 daily.time 数组")

    hourly_indexes_by_day: dict[date, list[int]] = {}

    for index, timestamp in enumerate(hourly_times):
        if not isinstance(timestamp, str):
            raise ValueError("hourly.time 必须包含字符串")

        try:
            forecast_date = date.fromisoformat(timestamp[:10])
        except ValueError as error:
            raise ValueError(
                f"无效的小时天气时间：{timestamp}"
            ) from error

        hourly_indexes_by_day.setdefault(
            forecast_date,
            [],
        ).append(index)

    daily_index_by_day: dict[date, int] = {}

    for index, date_value in enumerate(daily_times):
        if not isinstance(date_value, str):
            raise ValueError("daily.time 必须包含字符串")

        try:
            forecast_date = date.fromisoformat(date_value)
        except ValueError as error:
            raise ValueError(
                f"无效的每日天气日期：{date_value}"
            ) from error

        daily_index_by_day[forecast_date] = index

    metadata = {
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get(
            "timezone_abbreviation"
        ),
        "utc_offset_seconds": payload.get(
            "utc_offset_seconds"
        ),
        "hourly_units": payload.get("hourly_units"),
        "daily_units": payload.get("daily_units"),
    }

    result: dict[date, dict] = {}

    for forecast_date, hourly_indexes in (
        hourly_indexes_by_day.items()
    ):
        daily_index = daily_index_by_day.get(forecast_date)
        if daily_index is None:
            raise ValueError(
                f"{forecast_date.isoformat()} 缺少 daily 数据"
            )

        result[forecast_date] = {
            **metadata,
            "hourly": _slice_parallel_arrays(
                hourly,
                hourly_indexes,
            ),
            "daily": _slice_parallel_arrays(
                daily,
                [daily_index],
            ),
        }

    return result


def _merge_parallel_arrays(
    parts: list[dict[str, list]],
) -> dict[str, list]:
    """合并字段相同的多组并行数组。"""
    if not parts:
        raise ValueError("至少需要一组天气时间序列")

    expected_fields = set(parts[0])
    merged = {
        field: []
        for field in parts[0]
    }

    for part in parts:
        if set(part) != expected_fields:
            raise ValueError("待合并的天气字段不一致")

        times = part.get("time")
        if not isinstance(times, list):
            raise ValueError("天气时间序列缺少 time 数组")

        expected_length = len(times)

        for field, values in part.items():
            if not isinstance(values, list):
                raise ValueError(f"天气字段 {field} 必须是数组")
            if len(values) != expected_length:
                raise ValueError(
                    f"天气字段 {field} 与 time 数组长度不一致"
                )

            merged[field].extend(values)

    return merged


def merge_forecast_days(
    payloads_by_day: dict[date, dict],
) -> dict:
    """按日期顺序合并多天的天气预报 payload。"""
    if not payloads_by_day:
        raise ValueError("至少需要一天的天气预报")

    ordered_payloads: list[dict] = []

    for forecast_date in sorted(payloads_by_day):
        payload = payloads_by_day[forecast_date]
        hourly = payload.get("hourly")
        daily = payload.get("daily")

        if not isinstance(hourly, dict):
            raise ValueError(
                f"{forecast_date.isoformat()} 缺少 hourly 对象"
            )
        if not isinstance(daily, dict):
            raise ValueError(
                f"{forecast_date.isoformat()} 缺少 daily 对象"
            )

        ordered_payloads.append(payload)

    first_payload = ordered_payloads[0]

    return {
        "timezone": first_payload.get("timezone"),
        "timezone_abbreviation": first_payload.get(
            "timezone_abbreviation"
        ),
        "utc_offset_seconds": first_payload.get(
            "utc_offset_seconds"
        ),
        "hourly_units": first_payload.get("hourly_units"),
        "hourly": _merge_parallel_arrays(
            [
                payload["hourly"]
                for payload in ordered_payloads
            ]
        ),
        "daily_units": first_payload.get("daily_units"),
        "daily": _merge_parallel_arrays(
            [
                payload["daily"]
                for payload in ordered_payloads
            ]
        ),
    }


def get_forecast_with_cache(
    cache: WeatherCache,
    *,
    latitude: float,
    longitude: float,
    elevation: float | None,
    start_date: date,
    end_date: date,
    fetch_forecast: Callable[[date, date], dict],
    ttl_seconds: int,
) -> dict:
    """优先使用每日缓存，并请求缺失的预报日期。"""
    try:
        cached_days, missing_dates = load_cached_forecast_days(
            cache,
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            start_date=start_date,
            end_date=end_date,
        )
    except (RedisConnectionError, RedisTimeoutError):
        logger.warning(
            "Redis weather cache unavailable; fetching forecast upstream",
            exc_info=True,
        )
        return fetch_forecast(start_date, end_date)

    if missing_dates:
        fetch_start = missing_dates[0]
        fetch_end = missing_dates[-1]

        fetched_payload = fetch_forecast(
            fetch_start,
            fetch_end,
        )
        fetched_days = split_forecast_by_day(
            fetched_payload
        )

        payloads_by_key: dict[str, dict] = {}

        for forecast_date, payload in fetched_days.items():
            if not start_date <= forecast_date <= end_date:
                continue

            cached_days[forecast_date] = payload

            key = build_forecast_cache_key(
                latitude=latitude,
                longitude=longitude,
                elevation=elevation,
                forecast_date=forecast_date,
            )
            payloads_by_key[key] = payload

        try:
            cache.set_many(
                payloads_by_key,
                ttl_seconds=ttl_seconds,
            )
        except (RedisConnectionError, RedisTimeoutError):
            logger.warning(
                "Redis weather cache write failed; returning fresh forecast",
                exc_info=True,
            )

    requested_dates = [
        start_date + timedelta(days=offset)
        for offset in range(
            (end_date - start_date).days + 1
        )
    ]

    still_missing = [
        forecast_date
        for forecast_date in requested_dates
        if forecast_date not in cached_days
    ]
    if still_missing:
        missing_text = ", ".join(
            forecast_date.isoformat()
            for forecast_date in still_missing
        )
        raise ValueError(
            f"天气接口未返回所需日期：{missing_text}"
        )

    requested_payloads = {
        forecast_date: cached_days[forecast_date]
        for forecast_date in requested_dates
    }

    return merge_forecast_days(requested_payloads)


def get_current_with_cache(
    cache: WeatherCache,
    *,
    latitude: float,
    longitude: float,
    elevation: float | None,
    fetch_current: Callable[[], dict],
    ttl_seconds: int,
) -> dict:
    """优先返回当前天气缓存，未命中时请求并写回。"""
    key = build_current_cache_key(
        latitude=latitude,
        longitude=longitude,
        elevation=elevation,
    )

    try:
        cached_payload = cache.get(key)
    except (RedisConnectionError, RedisTimeoutError):
        logger.warning(
            "Redis weather cache unavailable; fetching current weather upstream",
            exc_info=True,
        )
        return fetch_current()

    if cached_payload is not None:
        return cached_payload

    payload = fetch_current()

    try:
        cache.set(
            key,
            payload,
            ttl_seconds=ttl_seconds,
        )
    except (RedisConnectionError, RedisTimeoutError):
        logger.warning(
            "Redis weather cache write failed; returning fresh current weather",
            exc_info=True,
        )

    return payload
