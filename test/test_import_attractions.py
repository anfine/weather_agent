import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "import_attractions.py"
SPEC = importlib.util.spec_from_file_location("import_attractions", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
import_attractions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(import_attractions)

RUNTIME_SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "build_runtime_attractions.py"
)
RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "build_runtime_attractions",
    RUNTIME_SCRIPT_PATH,
)
assert RUNTIME_SPEC is not None and RUNTIME_SPEC.loader is not None
build_runtime_attractions = importlib.util.module_from_spec(RUNTIME_SPEC)
RUNTIME_SPEC.loader.exec_module(build_runtime_attractions)


class AttractionImportTests(unittest.TestCase):
    def test_reads_all_rows_from_xlsx(self) -> None:
        rows = import_attractions.read_xlsx_rows(
            import_attractions.DEFAULT_INPUT
        )
        self.assertEqual(len(rows), 54)
        self.assertEqual(rows[0]["name"], "西安")
        self.assertEqual(rows[-1]["name"], "敦煌")

    def test_build_candidates_fixes_known_data_issues(self) -> None:
        rows = import_attractions.read_xlsx_rows(
            import_attractions.DEFAULT_INPUT
        )
        candidates = import_attractions.build_candidates(rows)
        by_name = {item["name"]: item for item in candidates}

        self.assertIn("神农架", by_name)
        self.assertIn("泸沽湖", by_name)
        self.assertNotIn("神农架-", by_name)
        self.assertNotIn("沪沽湖", by_name)

        self.assertEqual(by_name["华山"]["elevation_m"], 2154.9)
        self.assertEqual(by_name["黄山"]["longitude"], 118.183333)
        self.assertEqual(by_name["黄山"]["latitude"], 30.166667)
        self.assertEqual(by_name["黄山"]["elevation_m"], 1864.8)
        self.assertEqual(by_name["黄山"]["review_status"], "reviewed")
        self.assertEqual(by_name["长白山"]["review_status"], "needs_review")

    def test_coordinate_conversion_is_plausible(self) -> None:
        longitude, latitude = import_attractions.bd09_to_wgs84(116.41, 39.91)
        self.assertGreater(longitude, 116.39)
        self.assertLess(longitude, 116.41)
        self.assertGreater(latitude, 39.89)
        self.assertLess(latitude, 39.91)

    def test_runtime_dataset_contains_all_candidates(self) -> None:
        candidates = import_attractions.build_candidates(
            import_attractions.read_xlsx_rows(
                import_attractions.DEFAULT_INPUT
            )
        )
        existing = {
            "attractions": [
                {
                    "id": "curated-huashan",
                    "name": "华山",
                    "experience_tags": [],
                }
            ]
        }
        runtime = build_runtime_attractions.build_runtime_dataset(
            {"destinations": candidates},
            existing,
        )

        self.assertEqual(len(runtime["attractions"]), 54)
        by_name = {item["name"]: item for item in runtime["attractions"]}
        self.assertEqual(by_name["华山"]["id"], "curated-huashan")
        self.assertEqual(by_name["黄山"]["coverage"], "representative_point")
        self.assertEqual(by_name["四姑娘山"]["coverage"], "regional_reference")
        self.assertTrue(by_name["西湖"]["experience_tags"])


if __name__ == "__main__":
    unittest.main()
