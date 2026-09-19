import unittest
from datetime import datetime

from dashboard.provenance import iso_timestamp, source_latency_seconds


class ProvenanceTests(unittest.TestCase):
    def test_normalizes_provider_time_to_beijing_time(self):
        self.assertEqual(
            iso_timestamp("14:59:00", trade_date="2026-09-18"),
            "2026-09-18T14:59:00+08:00",
        )

    def test_calculates_non_negative_source_latency(self):
        received = datetime.fromisoformat("2026-09-18T15:00:05+08:00")
        self.assertEqual(source_latency_seconds("2026-09-18T15:00:00+08:00", received), 5.0)


if __name__ == "__main__":
    unittest.main()
