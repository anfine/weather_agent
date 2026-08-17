import json
import unittest
from datetime import date
from unittest.mock import Mock

from redis.exceptions import ConnectionError as RedisConnectionError

from weather_cache import (
    WeatherCache,
    build_current_cache_key,
    build_forecast_cache_key,
    get_current_with_cache,
    get_forecast_with_cache,
    load_cached_forecast_days,
    merge_forecast_days,
    split_forecast_by_day,
)


TEST_KEY = "weather_agent:weather:v1:test"


class WeatherCacheKeyTests(unittest.TestCase):
    def test_builds_stable_forecast_key(self) -> None:
        first = build_forecast_cache_key(
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            forecast_date=date(2026, 8, 18),
        )
        nearly_same = build_forecast_cache_key(
            latitude=31.2300001,
            longitude=121.4700001,
            elevation=None,
            forecast_date=date(2026, 8, 18),
        )
        sea_level = build_forecast_cache_key(
            latitude=31.23,
            longitude=121.47,
            elevation=0,
            forecast_date=date(2026, 8, 18),
        )

        self.assertEqual(
            first,
            (
                "weather_agent:weather:v1:forecast:"
                "31.230000:121.470000:auto:2026-08-18"
            ),
        )
        self.assertEqual(first, nearly_same)
        self.assertNotEqual(first, sea_level)

    def test_current_and_forecast_use_different_keys(self) -> None:
        current_key = build_current_cache_key(
            latitude=31.23,
            longitude=121.47,
            elevation=None,
        )
        forecast_key = build_forecast_cache_key(
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            forecast_date=date(2026, 8, 18),
        )

        self.assertIn(":current:", current_key)
        self.assertIn(":forecast:", forecast_key)
        self.assertNotEqual(current_key, forecast_key)


class WeatherCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Mock()
        self.cache = WeatherCache(self.client)

    def test_get_returns_none_when_key_is_missing(self) -> None:
        self.client.get.return_value = None

        result = self.cache.get(TEST_KEY)

        self.assertIsNone(result)
        self.client.delete.assert_not_called()

    def test_get_deserializes_cached_payload(self) -> None:
        self.client.get.return_value = (
            '{"timezone":"Asia/Shanghai","temperature":20}'
        )

        result = self.cache.get(TEST_KEY)

        self.assertEqual(
            result,
            {
                "timezone": "Asia/Shanghai",
                "temperature": 20,
            },
        )

    def test_get_removes_invalid_payload(self) -> None:
        invalid_values = [
            "not-json",
            '["valid JSON", "but not a dict"]',
        ]

        for cached_value in invalid_values:
            with self.subTest(cached_value=cached_value):
                self.client.reset_mock()
                self.client.get.return_value = cached_value

                result = self.cache.get(TEST_KEY)

                self.assertIsNone(result)
                self.client.delete.assert_called_once_with(TEST_KEY)

    def test_set_serializes_payload_with_ttl(self) -> None:
        payload = {
            "timezone": "Asia/Shanghai",
            "weather": "晴",
        }

        self.cache.set(
            TEST_KEY,
            payload,
            ttl_seconds=60,
        )

        self.client.set.assert_called_once()
        call = self.client.set.call_args

        self.assertEqual(call.args[0], TEST_KEY)
        self.assertEqual(
            json.loads(call.args[1]),
            payload,
        )
        self.assertNotIn(" ", call.args[1])
        self.assertEqual(call.kwargs, {"ex": 60})

    def test_set_rejects_nonpositive_ttl(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "缓存 TTL 必须大于 0",
        ):
            self.cache.set(
                TEST_KEY,
                {},
                ttl_seconds=0,
            )

        self.client.set.assert_not_called()

    def test_get_many_preserves_hits_and_misses(self) -> None:
        keys = [
            "weather:2026-08-17",
            "weather:2026-08-18",
        ]
        self.client.mget.return_value = [
            '{"daily":{"time":["2026-08-17"]}}',
            None,
        ]

        results = self.cache.get_many(keys)

        self.assertEqual(
            results,
            [
                {
                    "daily": {
                        "time": ["2026-08-17"],
                    },
                },
                None,
            ],
        )
        self.client.mget.assert_called_once_with(keys)

    def test_set_many_uses_pipeline_with_ttl(self) -> None:
        pipeline = Mock()
        self.client.pipeline.return_value = pipeline
        payloads_by_key = {
            "weather:2026-08-17": {
                "daily": {"time": ["2026-08-17"]},
            },
            "weather:2026-08-18": {
                "daily": {"time": ["2026-08-18"]},
            },
        }

        self.cache.set_many(
            payloads_by_key,
            ttl_seconds=300,
        )

        self.client.pipeline.assert_called_once_with(
            transaction=False
        )
        self.assertEqual(pipeline.set.call_count, 2)

        for actual_call, expected_item in zip(
            pipeline.set.call_args_list,
            payloads_by_key.items(),
        ):
            expected_key, expected_payload = expected_item
            self.assertEqual(actual_call.args[0], expected_key)
            self.assertEqual(
                json.loads(actual_call.args[1]),
                expected_payload,
            )
            self.assertEqual(actual_call.kwargs, {"ex": 300})

        pipeline.execute.assert_called_once_with()

    def test_load_cached_forecast_days_separates_misses(self) -> None:
        first_day = {
            "daily": {"time": ["2026-08-17"]},
        }
        third_day = {
            "daily": {"time": ["2026-08-19"]},
        }
        self.client.mget.return_value = [
            json.dumps(first_day),
            None,
            json.dumps(third_day),
        ]

        cached_days, missing_dates = load_cached_forecast_days(
            self.cache,
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 19),
        )

        self.assertEqual(
            cached_days,
            {
                date(2026, 8, 17): first_day,
                date(2026, 8, 19): third_day,
            },
        )
        self.assertEqual(
            missing_dates,
            [date(2026, 8, 18)],
        )
        self.client.mget.assert_called_once_with(
            [
                (
                    "weather_agent:weather:v1:forecast:"
                    "31.230000:121.470000:auto:2026-08-17"
                ),
                (
                    "weather_agent:weather:v1:forecast:"
                    "31.230000:121.470000:auto:2026-08-18"
                ),
                (
                    "weather_agent:weather:v1:forecast:"
                    "31.230000:121.470000:auto:2026-08-19"
                ),
            ]
        )


class ForecastPayloadTests(unittest.TestCase):
    def test_split_and_merge_forecast_payload(self) -> None:
        original_payload = {
            "timezone": "Asia/Shanghai",
            "timezone_abbreviation": "GMT+8",
            "utc_offset_seconds": 28800,
            "hourly_units": {
                "temperature_2m": "°C",
            },
            "hourly": {
                "time": [
                    "2026-08-17T00:00",
                    "2026-08-17T01:00",
                    "2026-08-18T00:00",
                    "2026-08-18T01:00",
                ],
                "temperature_2m": [
                    25.0,
                    24.5,
                    26.0,
                    25.5,
                ],
            },
            "daily_units": {
                "temperature_2m_max": "°C",
            },
            "daily": {
                "time": [
                    "2026-08-17",
                    "2026-08-18",
                ],
                "temperature_2m_max": [
                    31.0,
                    32.0,
                ],
            },
        }

        payloads_by_day = split_forecast_by_day(
            original_payload
        )

        self.assertEqual(
            set(payloads_by_day),
            {
                date(2026, 8, 17),
                date(2026, 8, 18),
            },
        )
        self.assertEqual(
            payloads_by_day[date(2026, 8, 17)]["hourly"]["time"],
            [
                "2026-08-17T00:00",
                "2026-08-17T01:00",
            ],
        )

        # 故意反序插入，确认 merge 会按日期重新排序。
        reversed_payloads = {
            date(2026, 8, 18): payloads_by_day[
                date(2026, 8, 18)
            ],
            date(2026, 8, 17): payloads_by_day[
                date(2026, 8, 17)
            ],
        }

        merged_payload = merge_forecast_days(
            reversed_payloads
        )

        self.assertEqual(merged_payload, original_payload)


class WeatherCacheAsideTests(unittest.TestCase):
    @staticmethod
    def _forecast_payload(*forecast_dates: date) -> dict:
        date_strings = [value.isoformat() for value in forecast_dates]
        return {
            "timezone": "Asia/Shanghai",
            "timezone_abbreviation": "GMT+8",
            "utc_offset_seconds": 28800,
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {
                "time": [f"{value}T12:00" for value in date_strings],
                "temperature_2m": [
                    20.0 + index
                    for index in range(len(date_strings))
                ],
            },
            "daily_units": {"temperature_2m_max": "°C"},
            "daily": {
                "time": date_strings,
                "temperature_2m_max": [
                    25.0 + index
                    for index in range(len(date_strings))
                ],
            },
        }

    def test_current_hit_skips_upstream(self) -> None:
        cache = Mock()
        cached_payload = {"current": {"temperature_2m": 25.0}}
        cache.get.return_value = cached_payload
        fetch_current = Mock()

        result = get_current_with_cache(
            cache,
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            fetch_current=fetch_current,
            ttl_seconds=300,
        )

        self.assertEqual(result, cached_payload)
        fetch_current.assert_not_called()
        cache.set.assert_not_called()

    def test_current_miss_fetches_and_writes_short_ttl(self) -> None:
        cache = Mock()
        cache.get.return_value = None
        fresh_payload = {"current": {"temperature_2m": 25.0}}
        fetch_current = Mock(return_value=fresh_payload)

        result = get_current_with_cache(
            cache,
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            fetch_current=fetch_current,
            ttl_seconds=300,
        )

        self.assertEqual(result, fresh_payload)
        fetch_current.assert_called_once_with()
        cache.set.assert_called_once_with(
            (
                "weather_agent:weather:v1:current:"
                "31.230000:121.470000:auto"
            ),
            fresh_payload,
            ttl_seconds=300,
        )

    def test_upstream_error_is_not_cached(self) -> None:
        cache = Mock()
        cache.get.return_value = None
        fetch_current = Mock(side_effect=RuntimeError("upstream failed"))

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            get_current_with_cache(
                cache,
                latitude=31.23,
                longitude=121.47,
                elevation=None,
                fetch_current=fetch_current,
                ttl_seconds=300,
            )

        cache.set.assert_not_called()

    def test_current_falls_back_when_redis_is_unavailable(self) -> None:
        cache = Mock()
        cache.get.side_effect = RedisConnectionError("unavailable")
        fresh_payload = {"current": {"temperature_2m": 25.0}}
        fetch_current = Mock(return_value=fresh_payload)

        with self.assertLogs("weather_cache", level="WARNING"):
            result = get_current_with_cache(
                cache,
                latitude=31.23,
                longitude=121.47,
                elevation=None,
                fetch_current=fetch_current,
                ttl_seconds=300,
            )

        self.assertEqual(result, fresh_payload)
        fetch_current.assert_called_once_with()
        cache.set.assert_not_called()

    def test_forecast_hit_skips_upstream(self) -> None:
        first = date(2026, 8, 17)
        second = date(2026, 8, 18)
        cached_payload = self._forecast_payload(first, second)
        cached_days = split_forecast_by_day(cached_payload)
        cache = Mock()
        cache.get_many.return_value = [
            cached_days[first],
            cached_days[second],
        ]
        fetch_forecast = Mock()

        result = get_forecast_with_cache(
            cache,
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            start_date=first,
            end_date=second,
            fetch_forecast=fetch_forecast,
            ttl_seconds=1800,
        )

        self.assertEqual(result, cached_payload)
        fetch_forecast.assert_not_called()
        cache.set_many.assert_not_called()

    def test_forecast_miss_fetches_missing_range_and_writes(self) -> None:
        first = date(2026, 8, 17)
        second = date(2026, 8, 18)
        first_payload = self._forecast_payload(first)
        second_payload = self._forecast_payload(second)
        cache = Mock()
        cache.get_many.return_value = [
            split_forecast_by_day(first_payload)[first],
            None,
        ]
        fetch_forecast = Mock(return_value=second_payload)

        result = get_forecast_with_cache(
            cache,
            latitude=31.23,
            longitude=121.47,
            elevation=None,
            start_date=first,
            end_date=second,
            fetch_forecast=fetch_forecast,
            ttl_seconds=1800,
        )

        fetch_forecast.assert_called_once_with(second, second)
        self.assertEqual(
            result["hourly"]["time"],
            [f"{first.isoformat()}T12:00", f"{second.isoformat()}T12:00"],
        )
        written = cache.set_many.call_args.args[0]
        self.assertEqual(
            list(written),
            [
                (
                    "weather_agent:weather:v1:forecast:"
                    "31.230000:121.470000:auto:2026-08-18"
                )
            ],
        )
        self.assertEqual(
            cache.set_many.call_args.kwargs,
            {"ttl_seconds": 1800},
        )

    def test_forecast_falls_back_when_redis_is_unavailable(self) -> None:
        first = date(2026, 8, 17)
        second = date(2026, 8, 18)
        cache = Mock()
        cache.get_many.side_effect = RedisConnectionError("unavailable")
        fresh_payload = self._forecast_payload(first, second)
        fetch_forecast = Mock(return_value=fresh_payload)

        with self.assertLogs("weather_cache", level="WARNING"):
            result = get_forecast_with_cache(
                cache,
                latitude=31.23,
                longitude=121.47,
                elevation=None,
                start_date=first,
                end_date=second,
                fetch_forecast=fetch_forecast,
                ttl_seconds=1800,
            )

        self.assertEqual(result, fresh_payload)
        fetch_forecast.assert_called_once_with(first, second)
        cache.set_many.assert_not_called()

    def test_forecast_upstream_error_is_not_cached(self) -> None:
        forecast_date = date(2026, 8, 17)
        cache = Mock()
        cache.get_many.return_value = [None]
        fetch_forecast = Mock(
            side_effect=RuntimeError("upstream failed")
        )

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            get_forecast_with_cache(
                cache,
                latitude=31.23,
                longitude=121.47,
                elevation=None,
                start_date=forecast_date,
                end_date=forecast_date,
                fetch_forecast=fetch_forecast,
                ttl_seconds=1800,
            )

        cache.set_many.assert_not_called()
