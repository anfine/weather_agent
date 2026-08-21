import json
import unittest
from unittest.mock import Mock, call

from attraction_cache import (
    AttractionCache,
    build_attraction_cache_key,
)


class AttractionCacheKeyTests(unittest.TestCase):
    def test_key_is_stable_after_query_normalization(self) -> None:
        self.assertEqual(
            build_attraction_cache_key("华山"),
            build_attraction_cache_key(" 华山 "),
        )
        self.assertNotEqual(
            build_attraction_cache_key("华山"),
            build_attraction_cache_key("西岳"),
        )

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能为空"):
            build_attraction_cache_key("   ")


class AttractionCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Mock()
        self.cache = AttractionCache(self.client)

    def test_get_returns_none_for_cache_miss(self) -> None:
        self.client.get.return_value = None

        self.assertIsNone(self.cache.get("华山"))

    def test_get_deserializes_found_and_not_found_entries(self) -> None:
        entries = [
            {
                "status": "found",
                "attraction": {"id": "huashan", "name": "华山"},
            },
            {"status": "not_found"},
        ]

        for entry in entries:
            with self.subTest(status=entry["status"]):
                self.client.get.return_value = json.dumps(entry)
                self.assertEqual(self.cache.get("华山"), entry)

    def test_get_removes_invalid_entries(self) -> None:
        invalid_entries = [
            "not-json",
            '["not", "a", "dict"]',
            '{"status":"unknown"}',
            '{"status":"found","attraction":null}',
        ]
        key = build_attraction_cache_key("华山")

        for entry in invalid_entries:
            with self.subTest(entry=entry):
                self.client.reset_mock()
                self.client.get.return_value = entry

                self.assertIsNone(self.cache.get("华山"))
                self.client.delete.assert_called_once_with(key)

    def test_set_found_serializes_payload_with_ttl(self) -> None:
        attraction = {"id": "huashan", "name": "华山"}

        self.cache.set_found("华山", attraction, ttl_seconds=21600)

        key = build_attraction_cache_key("华山")
        self.client.set.assert_called_once()
        call = self.client.set.call_args
        self.assertEqual(call.args[0], key)
        self.assertEqual(
            json.loads(call.args[1]),
            {"status": "found", "attraction": attraction},
        )
        self.assertEqual(call.kwargs, {"ex": 21600})

    def test_set_not_found_uses_short_ttl(self) -> None:
        self.cache.set_not_found("未知景点", ttl_seconds=300)

        key = build_attraction_cache_key("未知景点")
        self.client.set.assert_called_once()
        call = self.client.set.call_args
        self.assertEqual(call.args[0], key)
        self.assertEqual(
            json.loads(call.args[1]),
            {"status": "not_found"},
        )
        self.assertEqual(call.kwargs, {"ex": 300})

    def test_non_positive_ttl_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "TTL"):
            self.cache.set_not_found("未知景点", ttl_seconds=0)

    def test_clear_all_scans_and_deletes_in_batches(self) -> None:
        self.client.scan_iter.return_value = [
            "attraction-key-1",
            "attraction-key-2",
            "attraction-key-3",
        ]
        self.client.delete.side_effect = [2, 1]

        deleted_count = self.cache.clear_all(batch_size=2)

        self.assertEqual(deleted_count, 3)
        self.client.scan_iter.assert_called_once_with(
            match="weather_agent:attraction:v1:query:*",
            count=2,
        )
        self.assertEqual(
            self.client.delete.call_args_list,
            [
                call("attraction-key-1", "attraction-key-2"),
                call("attraction-key-3"),
            ],
        )

    def test_clear_all_rejects_non_positive_batch_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "batch_size"):
            self.cache.clear_all(batch_size=0)


if __name__ == "__main__":
    unittest.main()
