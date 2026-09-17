import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard.builder import latest_successful_snapshot, previous_close_snapshot
from dashboard.scoring import phase_at


TZ = ZoneInfo("Asia/Shanghai")


def closed_snapshot():
    return {
        "status": "SUCCESS",
        "generated_at": "2026-09-15T15:05:00+08:00",
        "trade_date": "2026-09-15",
        "phase": {"code": "CLOSED", "label": "收盘复盘"},
        "market": {
            "regime": "NEUTRAL", "breadth": 0.5, "advancers": 500, "decliners": 500,
            "flat": 0, "median_pct": 0, "limit_up_count": 10, "limit_down_count": 3,
            "market_amount": 800_000_000_000, "continuity_available": True,
        },
        "sectors": [],
        "channels": {
            "ordinary": {"name": "普通隔夜", "open": False, "qualified_count": 1, "message": "收盘"},
            "hot": {"name": "热点波段", "open": False, "qualified_count": 1, "message": "收盘"},
        },
        "rankings": [{
            "name": "示例股份", "code": "600001", "score": 80,
            "modules": [], "plan": {"feasible": True},
            "channels": {"ordinary": {"actionable_now": True}, "hot": {"actionable_now": True}},
        }],
        "candidates": [{
            "name": "示例股份", "code": "600001", "score": 80,
            "modules": [], "plan": {"feasible": True},
            "channels": {"ordinary": {"actionable_now": True}, "hot": {"actionable_now": True}},
        }],
        "diagnostics": {},
    }


class PreopenTests(unittest.TestCase):
    def test_before_0925_uses_preopen_phase(self):
        phase = phase_at(datetime(2026, 9, 16, 9, 24, tzinfo=TZ))
        self.assertEqual(phase["code"], "PREOPEN")

    def test_0925_starts_current_auction_phase(self):
        phase = phase_at(datetime(2026, 9, 16, 9, 25, tzinfo=TZ))
        self.assertEqual(phase["code"], "AUCTION")

    def test_previous_close_disables_all_execution(self):
        result = previous_close_snapshot(closed_snapshot(), datetime(2026, 9, 16, 8, 30, tzinfo=TZ))
        self.assertEqual(result["data_context"]["code"], "PREVIOUS_CLOSE")
        self.assertEqual(result["trade_date"], "2026-09-15")
        self.assertFalse(result["channels"]["ordinary"]["open"])
        self.assertFalse(result["channels"]["hot"]["open"])
        self.assertFalse(result["candidates"][0]["channels"]["ordinary"]["actionable_now"])
        self.assertFalse(result["rankings"][0]["channels"]["hot"]["actionable_now"])
        self.assertIn("上一交易日收盘快照", result["analysis"]["summary"])

    def test_history_recovers_when_latest_snapshot_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory)
            (history / "2026-09-15-1505.json").write_text(json.dumps(closed_snapshot()), encoding="utf-8")
            result = latest_successful_snapshot({"status": "FAILED"}, history)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["trade_date"], "2026-09-15")


if __name__ == "__main__":
    unittest.main()
