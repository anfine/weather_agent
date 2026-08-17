import json
import os
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

from langchain.messages import ToolMessage

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

import main
import scoring


class WeatherToolTests(unittest.TestCase):
    def test_agent_prompt_disables_optional_follow_up_questions(self) -> None:
        self.assertIn("给出结论后直接结束", main.AGENT_SYSTEM_PROMPT)
        self.assertIn("只有缺少城市等必要信息", main.AGENT_SYSTEM_PROMPT)

    @patch("main.weather_cache")
    @patch("main.requests.get")
    def test_current_weather_keeps_timezone_metadata(
        self,
        get: Mock,
        weather_cache: Mock,
    ) -> None:
        weather_cache.get.return_value = None
        get.return_value.json.return_value = {
            "timezone": "Asia/Shanghai",
            "timezone_abbreviation": "GMT+8",
            "utc_offset_seconds": 28800,
            "current_units": {"temperature_2m": "°C"},
            "current": {"temperature_2m": 30.0},
        }

        result = main.get_weather.invoke(
            {"latitude": 31.23, "longitude": 121.47}
        )

        self.assertEqual(result["current"]["temperature_2m"], 30.0)
        params = get.call_args.kwargs["params"]
        self.assertIn("current", params)
        self.assertNotIn("hourly", params)
        self.assertNotIn("elevation", params)
        self.assertEqual(params["timezone"], "auto")
        self.assertEqual(params["temperature_unit"], "celsius")
        self.assertEqual(params["wind_speed_unit"], "kmh")
        self.assertEqual(params["precipitation_unit"], "mm")
        self.assertEqual(
            weather_cache.set.call_args.kwargs,
            {"ttl_seconds": main.CURRENT_WEATHER_CACHE_TTL_SECONDS},
        )

    @patch("main.weather_cache")
    @patch("main.requests.get")
    def test_forecast_requests_daily_and_hourly_fields(
        self,
        get: Mock,
        weather_cache: Mock,
    ) -> None:
        tomorrow = date.today() + timedelta(days=1)
        weather_cache.get_many.return_value = [None]
        get.return_value.json.return_value = {
            "timezone": "Asia/Shanghai",
            "timezone_abbreviation": "GMT+8",
            "utc_offset_seconds": 28800,
            "hourly_units": {},
            "hourly": {"time": [f"{tomorrow.isoformat()}T12:00"]},
            "daily_units": {},
            "daily": {"time": [tomorrow.isoformat()]},
        }

        result = main.get_weather.invoke(
            {
                "latitude": 39.9,
                "longitude": 116.4,
                "elevation": 43.5,
                "start_date": tomorrow.isoformat(),
                "end_date": tomorrow.isoformat(),
            }
        )

        self.assertIn("hourly", result)
        self.assertIn("daily", result)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["start_date"], tomorrow.isoformat())
        self.assertEqual(params["end_date"], tomorrow.isoformat())
        self.assertEqual(params["hourly"], ",".join(main.HOURLY_FIELDS))
        self.assertEqual(params["daily"], ",".join(main.DAILY_FIELDS))
        self.assertEqual(params["timezone"], "auto")
        self.assertEqual(params["elevation"], 43.5)
        self.assertEqual(
            weather_cache.set_many.call_args.kwargs,
            {"ttl_seconds": main.FORECAST_WEATHER_CACHE_TTL_SECONDS},
        )

    def test_tourism_weather_fields_are_requested(self) -> None:
        self.assertIn("relative_humidity_2m", main.CURRENT_FIELDS)
        self.assertIn("uv_index", main.CURRENT_FIELDS)
        self.assertIn("snowfall", main.CURRENT_FIELDS)
        self.assertIn("snow_depth", main.CURRENT_FIELDS)

        self.assertIn("relative_humidity_2m", main.HOURLY_FIELDS)
        self.assertIn("uv_index", main.HOURLY_FIELDS)
        self.assertIn("snowfall", main.HOURLY_FIELDS)
        self.assertIn("snow_depth", main.HOURLY_FIELDS)

        self.assertIn("relative_humidity_2m_mean", main.DAILY_FIELDS)
        self.assertIn("uv_index_max", main.DAILY_FIELDS)
        self.assertIn("snowfall_sum", main.DAILY_FIELDS)

    def test_forecast_rejects_dates_beyond_seven_days(self) -> None:
        day_eight = date.today() + timedelta(days=8)

        with self.assertRaisesRegex(ValueError, "只支持未来 7 天"):
            main.get_weather.invoke(
                {
                    "latitude": 30.67,
                    "longitude": 104.07,
                    "start_date": day_eight.isoformat(),
                    "end_date": day_eight.isoformat(),
                }
            )

    def test_forecast_requires_both_dates(self) -> None:
        tomorrow = date.today() + timedelta(days=1)

        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            main.get_weather.invoke(
                {
                    "latitude": 30.67,
                    "longitude": 104.07,
                    "start_date": tomorrow.isoformat(),
                }
            )

    @patch("main.get_weather")
    @patch("main.load_attraction")
    def test_evaluate_attraction_uses_weather_point_and_scoring(
        self,
        attraction_loader: Mock,
        weather_tool: Mock,
    ) -> None:
        target = date.today() + timedelta(days=1)
        target_text = target.isoformat()
        attraction_loader.return_value = scoring.load_attraction_from_json("华山")
        weather_tool.invoke.return_value = {
            "hourly": {
                "time": [
                    f"{target_text}T06:00",
                    f"{target_text}T17:00",
                ],
                "visibility": [25000, 25000],
                "cloud_cover": [30, 30],
                "precipitation": [0, 0],
                "relative_humidity_2m": [70, 70],
                "wind_gusts_10m": [25, 25],
                "apparent_temperature": [15, 15],
                "snow_depth": [0, 0],
                "uv_index": [4, 4],
                "wind_speed_10m": [10, 10],
            }
        }

        result = main.evaluate_attraction.invoke(
            {
                "attraction_name": "华山",
                "target_date": target_text,
            }
        )

        weather_args = weather_tool.invoke.call_args.args[0]
        self.assertEqual(weather_args["latitude"], 34.477799)
        self.assertEqual(weather_args["longitude"], 110.077847)
        self.assertEqual(weather_args["elevation"], 2154.9)
        self.assertEqual(weather_args["start_date"], target_text)
        self.assertEqual(weather_args["end_date"], target_text)
        self.assertEqual(result["attraction_name"], "华山")
        self.assertEqual(result["overall_score"], 92.8)
        self.assertEqual(result["weather_point"]["id"], "south_peak")

    @patch("main.get_weather")
    @patch("main.find_city")
    def test_city_fallback_uses_geocoding_and_only_outdoor_score(
        self,
        city_tool: Mock,
        weather_tool: Mock,
    ) -> None:
        target = date.today() + timedelta(days=1)
        target_text = target.isoformat()
        city_tool.invoke.return_value = {
            "name": "杭州",
            "latitude": 30.25,
            "longitude": 120.17,
            "elevation": 19.0,
            "timezone": "Asia/Shanghai",
        }
        weather_tool.invoke.return_value = {
            "hourly": {
                "time": [
                    f"{target_text}T06:00",
                    f"{target_text}T17:00",
                ],
                "apparent_temperature": [15, 15],
                "precipitation": [0, 0],
                "wind_speed_10m": [10, 10],
                "uv_index": [4, 4],
            }
        }

        result = main.evaluate_city_outdoor.invoke(
            {
                "city_name": "杭州",
                "target_date": target_text,
                "requested_place": "灵隐寺",
                "city_resolution": "llm_inferred",
            }
        )

        city_tool.invoke.assert_called_once_with({"city": "杭州"})
        weather_args = weather_tool.invoke.call_args.args[0]
        self.assertEqual(weather_args["latitude"], 30.25)
        self.assertEqual(weather_args["longitude"], 120.17)
        self.assertEqual(weather_args["elevation"], 19.0)
        self.assertEqual(result["coverage"], "city_fallback")
        self.assertEqual(result["resolved_city"], "杭州")
        self.assertEqual(result["city_resolution"], "llm_inferred")
        self.assertEqual(len(result["experiences"]), 1)
        self.assertEqual(result["experiences"][0]["id"], "outdoor_visit")
        self.assertIn("灵隐寺尚未收录", result["weather_notice"])

    @patch("main.get_weather")
    @patch("main.load_attraction")
    def test_unknown_attraction_returns_not_found_without_weather(
        self,
        attraction_loader: Mock,
        weather_tool: Mock,
    ) -> None:
        attraction_loader.side_effect = ValueError("找不到景点")
        result = main.evaluate_attraction.invoke(
            {
                "attraction_name": "不存在的测试景点",
                "target_date": (date.today() + timedelta(days=1)).isoformat(),
            }
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["requested_place"], "不存在的测试景点")
        weather_tool.invoke.assert_not_called()

    def test_not_found_tool_message_requires_city_follow_up(self) -> None:
        messages = [
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "not_found",
                        "requested_place": "老君山",
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="attraction-call",
                name="evaluate_attraction",
            )
        ]

        self.assertTrue(main.needs_city_follow_up(messages))

    def test_successful_city_fallback_finishes_follow_up(self) -> None:
        messages = [
            ToolMessage(
                content="{'status': 'not_found', 'requested_place': '老君山'}",
                tool_call_id="attraction-call",
                name="evaluate_attraction",
            ),
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "ok",
                        "coverage": "city_fallback",
                    }
                ),
                tool_call_id="city-call",
                name="evaluate_city_outdoor",
            ),
        ]

        self.assertFalse(main.needs_city_follow_up(messages))


if __name__ == "__main__":
    unittest.main()
