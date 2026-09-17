import copy
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.scoring import market_summary, score_candidate


TZ = ZoneInfo("Asia/Shanghai")


def config():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parents[1] / "config/scoring_candidate.json").read_text())


def quote():
    return {
        "code": "600001",
        "name": "示例股份",
        "industry": "测试行业",
        "price": 11.0,
        "change_pct": 4.0,
        "amount": 800_000_000,
        "turnover_rate": 5.0,
        "high": 11.1,
        "low": 10.3,
        "open": 10.5,
        "previous_close": 10.58,
        "market_time": "2026-09-15T14:30:00+08:00",
    }


def detail():
    rows = []
    for index in range(30):
        close = 9.2 + index * 0.055
        rows.append({"date": f"2026-08-{index + 1:02d}" if index < 15 else f"2026-09-{index - 14:02d}", "open": close - 0.08, "close": close, "high": close + 0.12, "low": close - 0.14, "volume": 1000 + index * 20})
    rows[-1].update({"date": "2026-09-15", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.3, "volume": 2200})
    minutes = []
    for index in range(60):
        value = 10.55 + index * 0.0075
        minutes.append({"time": f"2026-09-15T{13 + (30 + index) // 60:02d}:{(30 + index) % 60:02d}+08:00", "open": value - 0.01, "close": value, "high": value + 0.02, "low": value - 0.02, "volume": 100 + index})
    return {"daily": rows, "minute": minutes}


def sector(previous=True):
    return {"sector": "测试行业", "members": 30, "median_pct": 2.1, "breadth": 0.76, "amount": 20_000_000_000, "limit_up_count": 3, "relative_strength": 2.4, "amount_percentile": 0.9, "strong": True, "previous_strong": previous, "confirmed": previous}


class ScoringTests(unittest.TestCase):
    def test_module_weights_sum_to_100(self):
        self.assertEqual(sum(config()["weights"].values()), 100)

    def test_missing_previous_sector_snapshot_cannot_pass_d(self):
        result = score_candidate(quote(), detail(), sector(False), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        module_d = next(item for item in result["modules"] if item["key"] == "D")
        self.assertEqual(module_d["state"], "数据不足")
        self.assertLessEqual(module_d["score"], 10)
        self.assertFalse(result["channels"]["hot"]["qualified"])

    def test_risk_off_closes_ordinary_even_with_strong_stock(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_OFF"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertFalse(result["channels"]["ordinary"]["qualified"])

    def test_score_is_exact_sum_of_visible_modules(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertEqual(result["score"], sum(item["score"] for item in result["modules"]))
        self.assertEqual([item["key"] for item in result["modules"]], ["A", "B", "C", "D", "E", "G"])

    def test_tail_momentum_is_not_scored_before_1420(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 19, tzinfo=TZ))
        module_b = next(item for item in result["modules"] if item["key"] == "B")
        self.assertEqual(module_b["score"], 0)
        self.assertEqual(module_b["state"], "数据不足")
        self.assertIn("14:20前", module_b["evidence"][0])

    def test_tail_momentum_can_be_scored_at_1420(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 20, tzinfo=TZ))
        module_b = next(item for item in result["modules"] if item["key"] == "B")
        self.assertGreater(module_b["score"], 0)
        self.assertNotIn("14:20前", module_b["evidence"][0])

    def test_all_announcement_sources_failed_scores_zero_without_blocking_candidate(self):
        result = score_candidate(
            quote(),
            detail(),
            sector(True),
            {"regime": "RISK_ON"},
            config(),
            "2026-09-15",
            announcement_error="all sources failed",
            now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ),
        )
        module_e = next(item for item in result["modules"] if item["key"] == "E")
        self.assertEqual(module_e["score"], 0)
        self.assertEqual(module_e["state"], "数据不足")
        self.assertNotEqual(result["level"], "排除")

    def test_unchecked_announcement_is_not_treated_as_verified_empty(self):
        result = score_candidate(
            quote(),
            detail(),
            sector(True),
            {"regime": "RISK_ON"},
            config(),
            "2026-09-15",
            announcement_checked=False,
            now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ),
        )
        module_e = next(item for item in result["modules"] if item["key"] == "E")
        self.assertEqual(module_e["score"], 0)
        self.assertIn("未进入公告复核范围", module_e["evidence"][0])

    def test_failed_modules_never_exceed_40_percent(self):
        risky = quote()
        risky["turnover_rate"] = 30.0
        result = score_candidate(risky, detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        for module in result["modules"]:
            if module["state"] == "不通过":
                self.assertLessEqual(module["score"], int(module["max"] * 0.4))

    def test_impossible_trigger_range_blocks_both_channels(self):
        blocked = detail()
        blocked["daily"][-2]["high"] = 20.0
        result = score_candidate(quote(), blocked, sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertFalse(result["plan"]["feasible"])
        self.assertFalse(result["channels"]["ordinary"]["qualified"])
        self.assertFalse(result["channels"]["hot"]["qualified"])

    def test_market_sector_needs_previous_confirmation(self):
        rows = []
        for index in range(20):
            rows.append({"code": f"600{index:03d}", "name": "样本", "change_pct": 2 if index < 16 else -1, "amount": 300_000_000, "industry": "测试行业", "market_time": "2026-09-15T10:30:00+08:00"})
        for index in range(20, 40):
            rows.append({"code": f"600{index:03d}", "name": "对照", "change_pct": -2 if index < 36 else 1, "amount": 200_000_000, "industry": "弱势行业", "market_time": "2026-09-15T10:30:00+08:00"})
        summary, current = market_summary(rows, None, config())
        self.assertFalse(current["items"]["测试行业"]["confirmed"])
        previous = {"generated_at": "2026-09-15T10:25:00+08:00", "sectors": current["public"]}
        _, confirmed = market_summary(rows, previous, config())
        self.assertTrue(confirmed["items"]["测试行业"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
