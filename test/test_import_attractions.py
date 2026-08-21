import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from database import Base
from models import Attraction, AttractionExperienceTag, WeatherPoint
from scripts.import_attractions import (
    DEFAULT_INPUT,
    build_attraction_id,
    import_nationwide_attractions,
    load_nationwide_sources,
    parse_attraction_source,
    read_xlsx_rows,
    sync_nationwide_attractions,
)


class NationwideAttractionParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = read_xlsx_rows(DEFAULT_INPUT)
        cls.sources, cls.report = load_nationwide_sources(DEFAULT_INPUT)

    def test_reads_all_fourteen_columns(self) -> None:
        self.assertEqual(len(self.rows), 14_847)
        first = self.rows[0]
        self.assertEqual(first["name"], "恭王府")
        self.assertEqual(first["grade"], "5A")
        self.assertEqual(first["province"], "北京")
        self.assertEqual(first["city"], "北京市")
        self.assertEqual(first["district"], "西城区")
        self.assertEqual(first["longitude"], "116.3800742")
        self.assertEqual(first["latitude"], "39.935814409999999")

    def test_uses_wgs84_coordinates_without_conversion(self) -> None:
        source = parse_attraction_source(self.rows[0])

        self.assertEqual(source.longitude, 116.3800742)
        self.assertEqual(source.latitude, 39.93581441)

    def test_business_id_is_stable_and_ignores_grade(self) -> None:
        first = build_attraction_id(
            province="云南",
            name="牟定化佛山景区",
            address="云南楚雄州牟定化佛山景区",
        )
        normalized = build_attraction_id(
            province=" 云南 ",
            name="牟定化佛山景区",
            address="云南楚雄州牟定化佛山景区",
        )

        self.assertEqual(first, normalized)
        self.assertRegex(first, r"^cn-scenic-[0-9a-f]{24}$")

    def test_merges_duplicates_and_selects_newer_grade_record(self) -> None:
        self.assertEqual(self.report["raw_rows"], 14_847)
        self.assertEqual(self.report["valid_rows"], 14_847)
        self.assertEqual(self.report["business_keys"], 14_840)
        self.assertEqual(self.report["duplicate_rows"], 7)
        self.assertEqual(self.report["conflict_groups"], 2)
        self.assertEqual(self.report["invalid_rows"], [])

        by_name = {source.name: source for source in self.sources}
        self.assertEqual(
            by_name["昆明寻甸柯渡红军长征纪念馆景区"].grade,
            "3A",
        )
        self.assertEqual(by_name["牟定化佛山景区"].grade, "3A")

    def test_rejects_invalid_wgs84_coordinate(self) -> None:
        row = dict(self.rows[0])
        row["longitude"] = "181"

        with self.assertRaisesRegex(ValueError, "longitude 超出范围"):
            parse_attraction_source(row)


class NationwideAttractionSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources, _ = load_nationwide_sources(DEFAULT_INPUT)

    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_sync_is_idempotent_and_creates_generic_weather_data(self) -> None:
        sample = self.sources[:3]
        with Session(self.engine) as session:
            first = sync_nationwide_attractions(session, sample)
            session.commit()

        with Session(self.engine) as session:
            second = sync_nationwide_attractions(session, sample)
            session.commit()

            attraction_count = session.scalar(
                select(func.count()).select_from(Attraction)
            )
            point_count = session.scalar(
                select(func.count()).select_from(WeatherPoint)
            )
            tag_count = session.scalar(
                select(func.count()).select_from(AttractionExperienceTag)
            )

        self.assertEqual(
            first,
            {
                "created": 3,
                "updated": 0,
                "skipped": 0,
                "merged_legacy": 0,
            },
        )
        self.assertEqual(
            second,
            {
                "created": 0,
                "updated": 0,
                "skipped": 3,
                "merged_legacy": 0,
            },
        )
        self.assertEqual(attraction_count, 3)
        self.assertEqual(point_count, 3)
        self.assertEqual(tag_count, 3)

    def test_import_clears_cache_after_database_transaction(self) -> None:
        events: list[str] = []
        transaction = MagicMock()
        transaction.__enter__.return_value = MagicMock()
        transaction.__exit__.side_effect = (
            lambda *args: events.append("transaction_finished")
        )

        with (
            patch(
                "scripts.import_attractions.load_nationwide_sources",
                return_value=([], {"raw_rows": 0}),
            ),
            patch(
                "scripts.import_attractions.session_scope",
                return_value=transaction,
            ),
            patch(
                "scripts.import_attractions.sync_nationwide_attractions",
                return_value={"created": 0},
            ),
            patch(
                "scripts.import_attractions.attraction_cache"
            ) as cache,
        ):
            cache.clear_all.side_effect = (
                lambda: events.append("cache_cleared") or 3
            )

            report = import_nationwide_attractions("sample.xlsx")

        self.assertEqual(
            events,
            ["transaction_finished", "cache_cleared"],
        )
        self.assertEqual(report["deleted_cache_entries"], 3)


if __name__ == "__main__":
    unittest.main()
