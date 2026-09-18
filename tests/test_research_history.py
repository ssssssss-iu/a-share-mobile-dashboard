import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard.research_history import record_research_snapshot


TZ = ZoneInfo("Asia/Shanghai")


def score(code, rank):
    return {
        "code": code,
        "name": f"股票{code}",
        "rank": rank,
        "score": 80 - rank,
        "price": 10.0,
        "in_score_pool": True,
        "modules": [{"key": "A", "score": 12, "max": 20, "state": "通过", "evidence": ["测试"]}],
        "channels": {
            "ordinary": {"qualified": True, "actionable_now": False},
            "hot": {"qualified": False, "actionable_now": False},
        },
        "plan": {"feasible": True},
        "data_confidence": {"score": 100, "max": 100, "level": "高"},
    }


def snapshot(trade_date, generated_at, phase="MORNING"):
    return {
        "status": "SUCCESS",
        "trade_date": trade_date,
        "generated_at": generated_at,
        "data_context": {"code": "CURRENT_SESSION"},
        "phase": {"code": phase},
        "market": {"regime": "RISK_ON"},
        "strategy": {"id": "test", "version": "2.2.0"},
    }


def quote_row(code, price, trade_date, open_price=None):
    return {
        "code": code,
        "price": price,
        "open": open_price if open_price is not None else price,
        "high": price + 0.5,
        "low": price - 0.5,
        "market_time": f"{trade_date}T10:10:00+08:00",
    }


class ResearchHistoryTests(unittest.TestCase):
    def test_saves_all_scores_and_backfills_forward_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_now = datetime(2026, 9, 17, 9, 25, tzinfo=TZ)
            first = record_research_snapshot(
                snapshot("2026-09-17", first_now.isoformat()),
                [score("600001", 1), score("600002", 2)],
                [quote_row("600001", 10.0, "2026-09-17"), quote_row("600002", 10.0, "2026-09-17")],
                root,
                first_now,
                trigger="cloudflare",
                scheduled_time=str(int(first_now.timestamp() * 1000)),
            )
            self.assertTrue(first["saved"])
            first_path = root / "2026-09-17" / "scheduled-0925.json"
            self.assertEqual(len(json.loads(first_path.read_text())["signals"]), 2)

            later_now = datetime(2026, 9, 17, 9, 55, tzinfo=TZ)
            record_research_snapshot(
                snapshot("2026-09-17", later_now.isoformat()),
                [score("600001", 1)],
                [quote_row("600001", 10.5, "2026-09-17"), quote_row("600002", 9.8, "2026-09-17")],
                root,
                later_now,
                trigger="cloudflare",
                scheduled_time=str(int(later_now.timestamp() * 1000)),
            )
            updated = json.loads(first_path.read_text())
            self.assertEqual(updated["signals"][0]["labels"]["forward_30m"]["return_pct"], 5.0)

            next_now = datetime(2026, 9, 18, 10, 10, tzinfo=TZ)
            record_research_snapshot(
                snapshot("2026-09-18", next_now.isoformat()),
                [score("600003", 1)],
                [quote_row("600001", 10.8, "2026-09-18", open_price=10.6), quote_row("600002", 10.1, "2026-09-18", open_price=10.0)],
                root,
                next_now,
                trigger="cloudflare",
                scheduled_time=str(int(next_now.timestamp() * 1000)),
            )
            updated = json.loads(first_path.read_text())
            labels = updated["signals"][0]["labels"]
            self.assertEqual(labels["next_open"]["return_pct"], 6.0)
            self.assertEqual(labels["next_1000"]["return_pct"], 8.0)

            for offset, trade_date in enumerate(("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"), start=2):
                phase = "CLOSED" if offset == 5 else "AFTERNOON"
                future_now = datetime.fromisoformat(f"{trade_date}T15:05:00+08:00")
                record_research_snapshot(
                    snapshot(trade_date, future_now.isoformat(), phase=phase),
                    [score(f"60000{offset + 2}", 1)],
                    [quote_row("600001", 10.0 + offset, trade_date)],
                    root,
                    future_now,
                    trigger="manual",
                )
            updated = json.loads(first_path.read_text())
            labels = updated["signals"][0]["labels"]
            self.assertEqual(labels["mfe_5d"]["status"], "OBSERVED")
            self.assertEqual(labels["mae_5d"]["status"], "OBSERVED")
            self.assertEqual(labels["mfe_5d"]["return_pct"], 55.0)
            self.assertEqual(labels["mae_5d"]["return_pct"], 3.0)


if __name__ == "__main__":
    unittest.main()
