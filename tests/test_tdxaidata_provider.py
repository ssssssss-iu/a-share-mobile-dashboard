import unittest

import pandas as pd

from dashboard.tdxaidata_bridge import _records_from_market_data
from dashboard.tdxaidata_provider import TdxAiDataSource, source_record, summarize_validation, tdx_symbol


class TdxAiDataProviderTests(unittest.TestCase):
    def test_symbol_mapping(self):
        self.assertEqual(tdx_symbol("600000"), "600000.SH")
        self.assertEqual(tdx_symbol("000001"), "000001.SZ")

    def test_missing_token_keeps_original_source(self):
        source = TdxAiDataSource({"enabled": True, "mode": "shadow"}, environ={})
        status = source.smoke()
        self.assertEqual(status["status"], "MISSING_TOKEN")
        self.assertIn("原数据源", status["message"])

    def test_validation_compares_quote_daily_and_minute(self):
        payload = {
            "status": "CONNECTED",
            "package_version": "1.1.0",
            "snapshots": {"600000.SH": {"Now": "10.01"}},
            "daily": {"600000.SH": [{"time": "2026-09-18T00:00:00", "close": 10.0}]},
            "minutes": {"600000.SH": [{"time": "2026-09-18T14:50:00", "close": 10.02}]},
            "auction": {},
        }
        quotes = {"600000": {"price": 10.0}}
        details = {
            "600000": {
                "daily": [{"date": "2026-09-18", "close": 10.0}],
                "minute": [{"time": "2026-09-18T14:50:00+08:00", "close": 10.0}],
            }
        }
        result = summarize_validation(payload, quotes, details, "2026-09-18", 0.30)
        self.assertEqual(result["status"], "CONNECTED")
        self.assertEqual(result["quote_match_ratio"], 1.0)
        self.assertEqual(result["daily_match_count"], 1)
        self.assertEqual(result["minute_match_count"], 1)

    def test_source_record_is_public_and_contains_no_token(self):
        record = source_record({"status": "CONNECTED", "mode": "shadow"})
        self.assertIn("影子验证", record["source"])
        self.assertNotIn("token", str(record).lower())

    def test_market_data_frames_are_normalized_for_json(self):
        index = pd.to_datetime(["2026-09-18 09:30:00", "2026-09-18 09:31:00"])
        payload = {
            "Open": pd.DataFrame({"600000.SH": [10.0, 10.1]}, index=index),
            "Close": pd.DataFrame({"600000.SH": [10.1, 10.2]}, index=index),
        }
        rows = _records_from_market_data(payload, ["600000.SH"])["600000.SH"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["close"], 10.2)
        self.assertTrue(rows[-1]["time"].startswith("2026-09-18T09:31:00"))


if __name__ == "__main__":
    unittest.main()
