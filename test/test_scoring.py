import unittest

import scoring


def make_hourly_fixture() -> dict[str, list[float | str]]:
    times = [
        "2026-08-01T05:00",
        "2026-08-01T06:00",
        "2026-08-01T17:00",
        "2026-08-01T18:00",
        "2026-08-02T06:00",
    ]
    return {
        "time": times,
        "visibility": [1000, 25000, 25000, 1000, 1000],
        "cloud_cover": [100, 30, 30, 100, 100],
        "precipitation": [20, 0, 0, 20, 20],
        "relative_humidity_2m": [100, 70, 70, 100, 100],
        "wind_gusts_10m": [80, 25, 25, 80, 80],
        "apparent_temperature": [-20, 15, 15, -20, -20],
        "snow_depth": [0.5, 0, 0, 0.5, 0.5],
        "uv_index": [12, 4, 4, 12, 12],
        "wind_speed_10m": [60, 10, 10, 60, 60],
    }


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = scoring.load_scoring_rules()
        cls.attraction = scoring.load_attraction("华山")
        cls.hourly = make_hourly_fixture()

    def test_load_attraction_by_alias(self) -> None:
        attraction = scoring.load_attraction("Mount Hua")
        self.assertEqual(attraction["id"], "cn-shaanxi-huashan")
        self.assertEqual(attraction["default_weather_point_id"], "south_peak")

    def test_score_filters_date_and_default_window(self) -> None:
        result = scoring.evaluate_attraction_weather(
            attraction=self.attraction,
            hourly=self.hourly,
            target_date="2026-08-01",
            rules=self.rules,
        )

        self.assertEqual(result["overall_score"], 92.8)
        scores = {
            experience["id"]: experience["score"]
            for experience in result["experiences"]
        }
        self.assertEqual(
            scores,
            {
                "scenic_view": 89.3,
                "hiking": 92.8,
                "outdoor_visit": 98.7,
            },
        )

    def test_metric_details_are_explainable(self) -> None:
        result = scoring.score_experience(
            hourly=self.hourly,
            target_date="2026-08-01",
            experience_id="scenic_view",
            rules=self.rules,
            start_time="06:00",
            end_time="18:00",
        )

        visibility = next(
            metric
            for metric in result["metrics"]
            if metric["field"] == "visibility"
        )
        self.assertEqual(visibility["value"], 25000.0)
        self.assertEqual(visibility["score"], 90.0)
        self.assertIn("visibility", result["positive_factors"])

    def test_missing_required_weather_field_is_rejected(self) -> None:
        incomplete_hourly = dict(self.hourly)
        del incomplete_hourly["visibility"]

        with self.assertRaisesRegex(ValueError, "缺少字段：visibility"):
            scoring.evaluate_attraction_weather(
                attraction=self.attraction,
                hourly=incomplete_hourly,
                target_date="2026-08-01",
                rules=self.rules,
            )

    def test_empty_evaluation_window_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有可用于评分"):
            scoring.evaluate_attraction_weather(
                attraction=self.attraction,
                hourly=self.hourly,
                target_date="2026-08-03",
                rules=self.rules,
            )


if __name__ == "__main__":
    unittest.main()
