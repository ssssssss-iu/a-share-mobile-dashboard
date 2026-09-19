import unittest
from datetime import datetime
from unittest.mock import patch

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

    def test_primary_data_replaces_quote_and_details(self):
        source = TdxAiDataSource(
            {"enabled": True, "mode": "primary", "primary_limit": 1},
            environ={"TDX_AI_DATA_TOKEN": "configured"},
        )
        payload = {
            "status": "CONNECTED",
            "package_version": "1.0.2",
            "snapshots": {"600000.SH": {"Now": 10.2, "LastClose": 10.0, "Open": 10.1, "Max": 10.3, "Min": 10.0, "Amount": 123456789}},
            "daily": {"600000.SH": [{"time": f"2026-09-{day:02d}T00:00:00", "open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9, "volume": 100} for day in range(1, 23)]},
            "minutes": {"600000.SH": [{"time": "2026-09-18T14:59:00", "open": 10.1, "close": 10.2, "high": 10.2, "low": 10.1, "volume": 10}]},
        }
        fallback_quotes = {"600000": {"code": "600000", "price": 9.9, "name": "测试", "change_pct": -1.0}}
        fallback_details = {"600000": {"daily": [], "minute": []}}
        with patch.object(source, "_call", return_value=payload):
            quotes, details, status = source.primary_data(
                ["600000"], "2026-09-18", "CLOSED", fallback_quotes, fallback_details,
                datetime.fromisoformat("2026-09-18T15:00:00+08:00"),
            )
        self.assertEqual(status["status"], "CONNECTED")
        self.assertEqual(status["primary_quote_count"], 1)
        self.assertEqual(quotes["600000"]["price"], 10.2)
        self.assertEqual(quotes["600000"]["data_source"], "TdxAiData")
        self.assertEqual(len(details["600000"]["daily"]), 22)
        self.assertEqual(details["600000"]["minute"][0]["close"], 10.2)


if __name__ == "__main__":
    unittest.main()
