import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard.intraday_history import record_intraday_snapshot


TZ = ZoneInfo("Asia/Shanghai")


def ranking(code, name, rank, score, module_d=10):
    return {
        "code": code,
        "name": name,
        "sector": "测试板块",
        "rank": rank,
        "score": score,
        "price": 10 + rank,
        "change_pct": 2.5,
        "market_time": "2026-09-17T09:25:00+08:00",
        "in_candidate_pool": score >= 60,
        "modules": [
            {"key": "A", "label": "当日量价", "score": 15, "max": 20, "state": "通过"},
            {"key": "D", "label": "板块持续", "score": module_d, "max": 20, "state": "通过"},
        ],
        "plan": {"breakout": 12.0, "no_chase_above": 12.4, "invalid_below": 10.8},
        "channels": {"ordinary": {"qualified": True}, "hot": {"qualified": False}},
    }


def snapshot(generated_at, rankings):
    return {
        "status": "SUCCESS",
        "generated_at": generated_at,
        "trade_date": "2026-09-17",
        "data_context": {"code": "CURRENT_SESSION"},
        "strategy": {"id": "test", "version": "1", "automation_profile": "test"},
        "rankings": rankings,
        "sources": [{"source": "行情", "coverage": 0.98, "minimum_coverage": 0.9, "failed_pages": 1}],
    }


class IntradayHistoryTests(unittest.TestCase):
    def test_records_first_success_once_and_calculates_next_slot_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory)
            first = snapshot(
                "2026-09-17T09:27:00+08:00",
                [ranking("600001", "甲公司", 1, 75), ranking("600002", "乙公司", 2, 70)],
            )
            primary_time = datetime(2026, 9, 17, 9, 25, tzinfo=TZ)
            self.assertTrue(record_intraday_snapshot(
                first,
                history,
                datetime(2026, 9, 17, 9, 27, tzinfo=TZ),
                "cloudflare",
                str(int(primary_time.timestamp() * 1000)),
            ))

            duplicate = deepcopy(first)
            duplicate["generated_at"] = "2026-09-17T09:31:00+08:00"
            duplicate["rankings"][0]["score"] = 99
            self.assertFalse(record_intraday_snapshot(
                duplicate, history, datetime(2026, 9, 17, 9, 31, tzinfo=TZ), "schedule"
            ))

            second = snapshot(
                "2026-09-17T09:42:00+08:00",
                [ranking("600002", "乙公司", 1, 76, module_d=16), ranking("600003", "丙公司", 2, 68)],
            )
            second_time = datetime(2026, 9, 17, 9, 40, tzinfo=TZ)
            self.assertTrue(record_intraday_snapshot(
                second,
                history,
                datetime(2026, 9, 17, 9, 42, tzinfo=TZ),
                "cloudflare",
                str(int(second_time.timestamp() * 1000)),
            ))

            payload = json.loads((history / "2026-09-17.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["snapshots"]), 2)
            self.assertEqual(payload["snapshots"][0]["rankings"][0]["score"], 75)
            promoted = payload["snapshots"][1]["rankings"][0]
            self.assertEqual(promoted["change"]["type"], "UP")
            self.assertEqual(promoted["change"]["rank_delta"], 1)
            self.assertEqual(promoted["change"]["score_delta"], 6)
            self.assertIn("D 板块持续 +6分", promoted["change"]["summary"])
            self.assertEqual(promoted["first_seen"]["slot"], "09:25")
            self.assertEqual(payload["snapshots"][1]["exited"][0]["code"], "600001")

            index = json.loads((history / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["dates"][0]["snapshot_count"], 2)

    def test_previous_close_is_not_recorded_as_intraday_history(self):
        with tempfile.TemporaryDirectory() as directory:
            prior = snapshot("2026-09-17T08:30:00+08:00", [ranking("600001", "甲公司", 1, 75)])
            prior["data_context"]["code"] = "PREVIOUS_CLOSE"
            self.assertFalse(record_intraday_snapshot(
                prior, Path(directory), datetime(2026, 9, 17, 8, 30, tzinfo=TZ)
            ))


if __name__ == "__main__":
    unittest.main()
