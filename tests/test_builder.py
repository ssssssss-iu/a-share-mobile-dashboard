import unittest

from dashboard.builder import merge_tdx_status


class BuilderTests(unittest.TestCase):
    def test_merge_tdx_status_accumulates_expanded_batches(self):
        first = {
            "status": "CONNECTED",
            "primary_quote_count": 36,
            "primary_detail_count": 35,
            "fallback_quote_count": 0,
            "fallback_detail_count": 1,
            "requested_count": 36,
            "timestamp_source_counts": {"tdx": 36},
            "provenance": [{"code": "600000"}],
            "received_at": "2026-09-21T10:00:00+08:00",
            "provider_time_min": "2026-09-21T09:59:00+08:00",
            "provider_time_max": "2026-09-21T10:00:00+08:00",
        }
        second = {
            "status": "PRIMARY_WITH_FALLBACK",
            "primary_quote_count": 20,
            "primary_detail_count": 18,
            "fallback_quote_count": 4,
            "fallback_detail_count": 6,
            "requested_count": 24,
            "timestamp_source_counts": {"fallback_or_missing": 4},
            "provenance": [{"code": "600036"}],
            "received_at": "2026-09-21T10:01:00+08:00",
            "provider_time_min": "2026-09-21T10:00:00+08:00",
            "provider_time_max": "2026-09-21T10:01:00+08:00",
        }
        merged = merge_tdx_status(first, second)
        self.assertEqual(merged["requested_count"], 60)
        self.assertEqual(merged["primary_detail_count"], 53)
        self.assertEqual(merged["fallback_detail_count"], 7)
        self.assertEqual(merged["status"], "PRIMARY_WITH_FALLBACK")
        self.assertEqual(len(merged["provenance"]), 2)
        self.assertEqual(merged["timestamp_source_counts"]["fallback_or_missing"], 4)


if __name__ == "__main__":
    unittest.main()
