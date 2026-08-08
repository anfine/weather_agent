import os
import unittest


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import scoring
from database import Base
from repositories.attraction import attraction_to_payload, find_attraction
from scripts.seed_attractions import load_attractions, seed_attractions


def make_hourly_fixture() -> dict[str, list[float | str]]:
    return {
        "time": [
            "2026-08-01T06:00",
            "2026-08-01T17:00",
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


class AttractionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        with Session(cls.engine) as session:
            seed_attractions(session, load_attractions())
            session.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_finds_by_name_id_and_alias_with_children_loaded(self) -> None:
        with Session(self.engine) as session:
            by_name = find_attraction(session, "华山")
            by_id = find_attraction(session, "cn-shaanxi-huashan")
            by_alias = find_attraction(session, "mount hua")

            self.assertIsNotNone(by_name)
            self.assertIs(by_name, by_id)
            self.assertIs(by_name, by_alias)
            unloaded = inspect(by_name).unloaded
            self.assertNotIn("aliases", unloaded)
            self.assertNotIn("weather_points", unloaded)
            self.assertNotIn("experience_tags", unloaded)

    def test_database_payload_produces_same_score_as_json(self) -> None:
        with Session(self.engine) as session:
            attraction = find_attraction(session, "西岳")
            self.assertIsNotNone(attraction)
            database_payload = attraction_to_payload(attraction)

        json_payload = scoring.load_attraction_from_json("华山")
        rules = scoring.load_scoring_rules()
        arguments = {
            "hourly": make_hourly_fixture(),
            "target_date": "2026-08-01",
            "rules": rules,
        }

        database_result = scoring.evaluate_attraction_weather(
            attraction=database_payload,
            **arguments,
        )
        json_result = scoring.evaluate_attraction_weather(
            attraction=json_payload,
            **arguments,
        )

        self.assertEqual(database_result, json_result)
        default_point_id = database_payload["default_weather_point_id"]
        default_point = next(
            point
            for point in database_payload["weather_points"]
            if point["id"] == default_point_id
        )
        self.assertEqual(default_point["name"], "南峰（落雁峰）")

    def test_unknown_attraction_returns_none(self) -> None:
        with Session(self.engine) as session:
            self.assertIsNone(find_attraction(session, "不存在的景点"))


if __name__ == "__main__":
    unittest.main()
