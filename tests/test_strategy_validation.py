import json
import tempfile
import unittest
from pathlib import Path

from dashboard.strategy_validation import validate_strategy


class StrategyValidationTests(unittest.TestCase):
    def test_deduplicates_stock_days_and_applies_cost_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = root / "2026-09-21"
            day.mkdir()
            signal = {
                "code": "600001",
                "generated_at": "2026-09-21T14:20:00+08:00",
                "score": 82,
                "channels": {"ordinary": {"qualified": True, "actionable_now": True}, "hot": {}},
                "labels": {"next_open": {"status": "OBSERVED", "return_pct": 1.0}},
            }
            for name, generated_at in (("scheduled-1420.json", signal["generated_at"]), ("scheduled-1435.json", "2026-09-21T14:35:00+08:00")):
                item = dict(signal)
                item["generated_at"] = generated_at
                (day / name).write_text(json.dumps({"trade_date": "2026-09-21", "signals": [item]}))
            result = validate_strategy(root, minimum_samples=2, round_trip_cost_bps=20)
            self.assertEqual(result["total_unique_stock_days"], 1)
            self.assertEqual(result["status"], "DATA_INSUFFICIENT")
            self.assertEqual(result["required_channel_observations"]["ordinary_actionable_next_open"], 1)
            self.assertEqual(result["required_channel_observations"]["hot_actionable_day3_close"], 0)
            metric = result["score_bands"]["80-100"]["next_open"]
            self.assertEqual(metric["observations"], 1)
            self.assertEqual(metric["net_mean_after_cost_pct"], 0.8)


if __name__ == "__main__":
    unittest.main()
